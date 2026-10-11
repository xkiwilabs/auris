from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import soundfile as sf
from fastapi import APIRouter, Depends, Form, Request, Response, UploadFile
from fastapi.responses import FileResponse

from auris.audio import io, reference
from auris.audio.asr_models import resolve_asr_model
from auris.log import get_logger
from auris.server import voice_access
from auris.server.asr_pool import TranscriptionJob
from auris.server.deps import get_asr_pool, get_db, get_settings
from auris.server.errors import BadAudio
from auris.server.schemas import ClipInfo, CloneResponse, VoiceInfo, VoiceUpdate
from auris.voice_library import cloning, storage, users
from auris.voice_library.exceptions import ClipNotFound
from auris.voice_library.resolve import is_preset, preset_voice_rows

router = APIRouter()
_log = get_logger("auris.server.voices")

_MIN_CLIP_SECONDS = 3.0
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # hard cap to guard RAM before decode


def _viewer(request: Request):
    return voice_access.viewer_for(request.state.principal)


@router.get("/v1/voices", response_model=list[VoiceInfo])
async def list_voices(request: Request, conn=Depends(get_db)):
    presets = [VoiceInfo(**row) for row in preset_voice_rows()]
    rows = await voice_access.list_voices(conn, _viewer(request))
    emails = await users.emails_by_id(conn, {o for (*_, o, _v) in rows if o})
    cloned = [
        VoiceInfo(
            name=n,
            clips=c,
            duration_s=d,
            default_model=m,
            kind="cloned",
            owner=emails.get(o),
            visibility=vis,
        )
        for (n, c, d, m, o, vis) in rows
    ]
    return presets + cloned


@router.post("/v1/voices/{name}/clone", status_code=201, response_model=CloneResponse)
async def clone_voice(
    request: Request,
    name: str,
    file: UploadFile,
    ref_text: str | None = Form(default=None),
    asr_model: str | None = Form(default=None),
    conn=Depends(get_db),
    settings=Depends(get_settings),
) -> CloneResponse:
    viewer = _viewer(request)
    # Refused before any audio work — in particular before the whisper decode below — so an
    # ownerless key never burns a GPU decode on a request that was always going to be refused.
    # Mirrors why pool.admit() exists at all: "a caller that would be rejected anyway never
    # allocates a decoded float32 array". find_for_clone calls the same check again further
    # down, so it stays correct on its own if ever called without this guard first.
    voice_access.refuse_if_ownerless(viewer)
    # Validate the STT choice before spending time on the upload (ValueError => 422).
    stt_model = resolve_asr_model(asr_model, settings)
    if is_preset(name):
        raise ValueError(f"{name!r} is a built-in preset name; choose a different voice name")
    # The permission decision, before `file.read()` and so before the decode, resample and GPU
    # whisper decode below — same principle as `refuse_if_ownerless` above and as
    # `pool.admit()`: a caller that would be rejected anyway never allocates a decoded float32
    # array. The refusal this one catches is the likely one: an ordinary account cloning onto a
    # colleague's *shared* name, which a per-owner namespace makes an easy mistake.
    await voice_access.find_for_clone(conn, name, viewer)
    raw = await file.read()
    if len(raw) > _MAX_UPLOAD_BYTES:
        raise BadAudio("upload too large")

    with tempfile.NamedTemporaryFile(suffix=Path(file.filename or "upload").suffix) as tmp:
        tmp.write(raw)
        tmp.flush()
        try:
            audio, sr = await asyncio.to_thread(io.load_audio, tmp.name)  # libsndfile + ffmpeg
        except (RuntimeError, sf.SoundFileError) as exc:
            # Log the underlying detail (ffmpeg stderr / paths) server-side only; the
            # client gets a generic message so internal details aren't leaked.
            _log.warning("audio decode failed for upload %r: %s", file.filename, exc)
            raise BadAudio("could not decode uploaded audio") from exc

    audio = io.resample(audio, sr, settings.sample_rate)
    if io.duration_s(audio, settings.sample_rate) > settings.max_upload_seconds:
        raise ValueError(f"audio exceeds max_upload_seconds ({settings.max_upload_seconds})")
    if io.duration_s(audio, settings.sample_rate) < _MIN_CLIP_SECONDS:
        raise ValueError(f"reference must be at least {_MIN_CLIP_SECONDS:.0f}s")

    decoded = None
    if not ref_text:
        # The pool is resolved here, not as a route dependency: a clone that supplies
        # ref_text needs no decode, so an STT-disabled server must still accept it
        # (get_asr_pool raises PipelineDisabled => 503 only on this branch).
        asr_pool = get_asr_pool(request)
        # Word timestamps, so the cut lands in a pause and the transcript covers exactly the
        # words kept; English and no VAD on purpose (see reference.clone_options).
        window = reference.decode_window(audio, settings.sample_rate)
        job = TranscriptionJob(window, settings.sample_rate, reference.clone_options(stt_model))
        decoded = await asr_pool.submit(job)
    # Trim + cap to ≤10 s, ending inside a pause — never part-way through a word, which is
    # what makes f5-TTS speak a stray word at the start of every generation.
    clip = await asyncio.to_thread(
        reference.reference_for_clone,
        audio,
        settings.sample_rate,
        ref_text=ref_text,
        decoded=decoded,
    )
    audio, transcript = clip.audio, clip.transcript
    seconds = io.duration_s(audio, settings.sample_rate)

    # A clone names a voice, but the name is only unique per owner now: someone else may already
    # have a voice by this name. find_for_clone raises Forbidden if the caller may not write it,
    # rather than silently appending the caller's clip to another account's voice.
    # Deliberately the *second* call — the early one above skips the audio work, this one is the
    # decision that counts, re-checked after the decode because a voice can be created or shared
    # in the window between the two. Both are wanted; keep them (the same reason
    # `refuse_if_ownerless` is called three times on this path).
    await voice_access.find_for_clone(conn, name, viewer)
    speaker = await cloning.get_or_create_speaker(conn, name, owner_id=viewer.user_id)
    index = await cloning.add_reference(
        conn, settings.voices_dir, speaker.id, audio, settings.sample_rate, transcript, seconds
    )
    return CloneResponse(
        voice=speaker.name,
        clip_index=index,
        transcript=transcript,
        duration_s=seconds,
        warnings=list(clip.warnings),
    )


@router.delete("/v1/voices/{name}")
async def delete_voice(
    request: Request,
    name: str,
    owner: str | None = None,
    conn=Depends(get_db),
    settings=Depends(get_settings),
):
    viewer = _viewer(request)
    speaker = await voice_access.get_for_write(
        conn, name, viewer, await voice_access.owner_id_for(conn, owner)
    )
    await voice_access.delete_voice(conn, speaker.id)
    speaker_dir = storage.clips_dir(settings.voices_dir, speaker.id).parent
    shutil.rmtree(speaker_dir, ignore_errors=True)
    return Response(status_code=200)


@router.get("/v1/voices/{name}/clips", response_model=list[ClipInfo])
async def list_clips(request: Request, name: str, owner: str | None = None, conn=Depends(get_db)):
    viewer = _viewer(request)
    speaker = await voice_access.get_for_read(
        conn, name, viewer, await voice_access.owner_id_for(conn, owner)
    )
    return [
        ClipInfo(
            index=storage.clip_index(c.filename),
            filename=c.filename,
            duration_s=c.duration_s or 0.0,
            transcript=c.transcript,
            created_at=c.created_at,
        )
        for c in await voice_access.get_clips(conn, speaker.id)
    ]


@router.get("/v1/voices/{name}/clips/{index}/audio")
async def clip_audio(
    request: Request,
    name: str,
    index: int,
    owner: str | None = None,
    conn=Depends(get_db),
    settings=Depends(get_settings),
):
    """The stored reference clip itself, so the dashboard can play back what a voice copies.

    Read access, exactly as listing the clips: shared voices are readable by everyone, and
    another account's private one is a 404 (from get_for_read), not a 403."""
    viewer = _viewer(request)
    speaker = await voice_access.get_for_read(
        conn, name, viewer, await voice_access.owner_id_for(conn, owner)
    )
    filename = f"clip_{index:03d}.wav"
    if await voice_access.get_clip_by_filename(conn, speaker.id, filename) is None:
        raise ClipNotFound(f"clip {index} not found for {name!r}")
    path = storage.clips_dir(settings.voices_dir, speaker.id) / filename
    if not path.is_file():  # a row whose WAV has gone: the same answer as no row at all
        raise ClipNotFound(f"clip {index} not found for {name!r}")
    return FileResponse(path, media_type="audio/wav", headers={"Cache-Control": "no-store"})


@router.delete("/v1/voices/{name}/clips/{index}")
async def delete_clip(
    request: Request,
    name: str,
    index: int,
    owner: str | None = None,
    conn=Depends(get_db),
    settings=Depends(get_settings),
):
    viewer = _viewer(request)
    speaker = await voice_access.get_for_write(
        conn, name, viewer, await voice_access.owner_id_for(conn, owner)
    )
    filename = f"clip_{index:03d}.wav"
    clip = await voice_access.get_clip_by_filename(conn, speaker.id, filename)
    if clip is None:
        raise ClipNotFound(f"clip {index} not found for {name!r}")
    if not await voice_access.delete_clip_if_not_last(conn, speaker.id, clip.id):
        # A voice with no clip can't synthesise (load_reference would raise on it).
        raise ValueError("this is the voice's only clip — delete the voice instead")
    # Row first, deliberately: a row pointing at a missing WAV would fail every synthesis for
    # this voice, whereas an orphaned WAV is only disk waste that delete_voice's rmtree reaps.
    try:
        storage.remove_clip(settings.voices_dir, speaker.id, filename)
    except OSError as exc:  # the row is already gone; a 500 here would strand a done deletion
        _log.warning("clip row deleted but its file remains", filename=filename, error=str(exc))
    remaining = await voice_access.get_clips(conn, speaker.id)
    return {"deleted": index, "remaining": len(remaining)}


@router.patch("/v1/voices/{name}", response_model=VoiceInfo)
async def update_voice(
    request: Request,
    name: str,
    body: VoiceUpdate,
    owner: str | None = None,
    conn=Depends(get_db),
):
    viewer = _viewer(request)
    speaker = await voice_access.get_for_write(
        conn, name, viewer, await voice_access.owner_id_for(conn, owner)
    )
    if body.visibility is not None:
        # ValueError => 422 via the installed handler: an invalid visibility value, or making an
        # unowned voice "private", which `_VISIBLE` would ignore (see voice_access).
        speaker = await voice_access.set_visibility(conn, speaker, body.visibility)
    if body.name is not None:
        if is_preset(body.name):
            raise ValueError(f"{body.name!r} is a built-in preset name; choose a different name")
        # Visibility is applied before the rename, so a request carrying both fields lands the
        # visibility change even if the rename below then raises NameTaken (409) — a partial
        # write. A single transaction across both would avoid that, but every writer in
        # voice_library keeps its success path await-free between its DML and commit(), and
        # that invariant is what makes the shared-connection rollback elsewhere in this package
        # safe. This is acceptable because the only caller that sends both fields in one PATCH
        # is a script, and retrying the whole request is idempotent.
        speaker = await voice_access.rename(conn, speaker.id, body.name)  # NameTaken => 409
    clips = await voice_access.get_clips(conn, speaker.id)
    emails = await users.emails_by_id(conn, {speaker.owner_id} if speaker.owner_id else set())
    return VoiceInfo(
        name=speaker.name,
        clips=len(clips),
        duration_s=sum(c.duration_s or 0.0 for c in clips),
        default_model=speaker.default_model,
        kind="cloned",
        owner=emails.get(speaker.owner_id),
        visibility=speaker.visibility,
    )
