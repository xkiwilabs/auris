// auris/server/static/js/voice-visibility.js — pure; decides who may flip a voice's visibility.
"use strict";

// An unowned voice's `visibility` column is inert: voice_library/speakers.py's `_VISIBLE` query
// shows every unowned row to every account regardless of what that column says, so toggling it
// would report success while changing nothing. `auris voices unshare` refuses on an unowned
// voice for exactly this reason — the dashboard must not offer a control the CLI refuses to
// perform, not even for an admin.
//
// `voice` carries `owner` (an email, or null); `me` is `state.me`; `isAdmin` is `state.admin`.
export function mayChangeVisibility(voice, me, isAdmin) {
  if (voice.owner == null) return false;
  if (isAdmin) return true;
  return !!me && !!me.email && voice.owner === me.email;
}

// Who may hear a voice's stored recordings: its owner and admins, as GET …/clips/{i}/audio
// (voice_access.get_for_listen) enforces. A shared voice is usable by everyone, its raw recording
// is not; an unowned one is admin-only, like every other change to it.
export function mayPlayClips(voice, me, isAdmin) {
  if (isAdmin) return true;
  if (voice.owner == null) return false;
  return !!me && !!me.email && voice.owner === me.email;
}
