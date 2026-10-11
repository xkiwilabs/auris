// auris/server/static/js/passages.js — what the New voice dialog offers to read, and its countdown.
// No DOM, so node can test it.
//
// Reading a passage is optional: the recording is transcribed either way, so going off-script
// is fine. A few short sentences with full stops between them suit cloning best — the server
// ends the clip in a pause, and full stops make natural ones. f5-TTS also copies the *style* of
// the reference, so a chatty read produces chattier speech than a formal one.
"use strict";

export const PASSAGES = [
  {
    // The opening of the Rainbow Passage — the same text `auris record` shows.
    title: "The Rainbow Passage",
    text: "When the sunlight strikes raindrops in the air, they act as a prism and form a " +
      "rainbow. The rainbow is a division of white light into many beautiful colors.",
  },
  {
    title: "Harvard sentences",
    text: "The birch canoe slid on the smooth planks. Glue the sheet to the dark blue " +
      "background. It's easy to tell the depth of a well.",
  },
  {
    title: "Harvard sentences",
    text: "The boy was there when the sun rose. A rod is used to catch pink salmon. The source " +
      "of the huge river is the clear spring.",
  },
  {
    title: "Harvard sentences",
    text: "Help the woman get back to her feet. A pot of tea helps to pass the evening. Smoky " +
      "fires lack flame and heat. The soft cushion broke the man's fall.",
  },
  {
    title: "Conversational",
    text: "Hi, it's good to hear from you. I've been meaning to call all week, but things got " +
      "busy. Let's catch up properly over coffee soon. How does Thursday sound?",
  },
];

export function nextPassage(index) {
  return index >= 0 ? (index + 1) % PASSAGES.length : 0;
}

// The mic is already recording while this runs, so every clip starts with a little room tone
// rather than on the first syllable.
const STEPS = [["Get ready…", 0.8], ["3", 0.6], ["2", 0.6], ["1", 0.6]];
export const COUNTDOWN_S = STEPS.reduce((n, [, s]) => n + s, 0);
const CUE_S = 1.5; // how long "Speak now" stays up

/** What to show `elapsed` seconds into a recording, and how much of it has been speaking time. */
export function countdownStep(elapsed) {
  let t = 0;
  for (const [label, seconds] of STEPS) {
    t += seconds;
    if (elapsed < t - 1e-9) return { label, speaking: false, spoken: 0 };
  }
  const spoken = Math.round((elapsed - COUNTDOWN_S) * 10) / 10;
  return { label: spoken < CUE_S ? "Speak now" : null, speaking: true, spoken: Math.max(0, spoken) };
}
