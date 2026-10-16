// Sleep Habits Check (practice/mock page at /sleep-check). General wellness copy only: everyday
// sleep habits, no breathing/snoring questions, no conditions, symptoms, levels, labels, numeric thresholds or medical advice.
// The banned-word scan in src/pages/SleepCheck.test.js runs over every string in this file.

export const SLEEP_COPY = {
  title: "Sleep Habits Check",
  badge: "Practice version",
  intro:
    "A quick, private check-in on everyday sleep habits. Answer 8 multiple-choice questions to get a few general tips.",
  privacy: "Your answers stay on this page only. Nothing is saved or sent anywhere, and reloading the page clears them.",
  progress: (n, total) => `${n} of ${total} answered`,
  submit: "See my tips",
  submitHint: "Answer every question to see your tips.",
  resultsTitle: "Your habit tips",
  resultsIntro: "Based on your answers, here are a few general habits you could try:",
  allSet: "Your answers already line up with common everyday sleep habits. Keep doing what works for you.",
  restart: "Start over",
  back: "Back to home",
  // Exact wording from the LegalEase review (sleep-quiz-wellness-review-prep-v1). Shown to every
  // user on the page and at the top of the results, whatever the answers.
  disclaimer:
    "This quiz is for general wellness only. It isn't a medical test and can't tell you whether you have any condition. If you're concerned about your sleep, talking with a doctor or other healthcare professional may be helpful.",
};

// Each option may point at one tip key. Tips are shown in question order: no ranking, no totals.
export const SLEEP_QUESTIONS = [
  { id: "wake", q: "How similar is your wake-up time from day to day?", options: [
    { label: "About the same most days" }, { label: "It varies a little", tip: "schedule" }, { label: "It varies a lot", tip: "schedule" } ] },
  { id: "winddown", q: "Do you have a wind-down routine in the evening?", options: [
    { label: "Most nights" }, { label: "Some nights", tip: "winddown" }, { label: "Rarely", tip: "winddown" } ] },
  { id: "screens", q: "How often do you use a phone, tablet or TV in bed or right before sleep?", options: [
    { label: "Rarely" }, { label: "Some nights", tip: "screens" }, { label: "Most nights", tip: "screens" } ] },
  { id: "caffeine", q: "When do you usually have your last coffee, tea, soda or energy drink?", options: [
    { label: "In the morning" }, { label: "Early afternoon" }, { label: "Late afternoon or evening", tip: "caffeine" }, { label: "I don't have caffeine" } ] },
  { id: "alcohol", q: "How often do you have alcohol in the few hours before bed?", options: [
    { label: "Rarely or never" }, { label: "Sometimes", tip: "alcohol" }, { label: "Often", tip: "alcohol" } ] },
  { id: "bedroom", q: "Which best describes your bedroom at night?", options: [
    { label: "Dark, quiet and comfortably cool" }, { label: "Some light or noise gets in", tip: "bedroom" }, { label: "Often bright, noisy or too warm", tip: "bedroom" } ] },
  { id: "daylight", q: "How much daylight and movement do you get during the day?", options: [
    { label: "Outdoors or active most days" }, { label: "Some days", tip: "daylight" }, { label: "Rarely", tip: "daylight" } ] },
  { id: "naps", q: "How do daytime naps fit into your day?", options: [
    { label: "I don't nap" }, { label: "Short naps, earlier in the day" }, { label: "Long naps or naps late in the day", tip: "naps" } ] },
];

export const SLEEP_TIPS = {
  schedule: "Try keeping a similar wake time each day, including weekends.",
  winddown: "Try a short, calming wind-down routine before bed, like reading, gentle stretching or dimming the lights.",
  screens: "Try putting phones and other devices away a little while before bed, or keeping them out of the bedroom.",
  caffeine: "Try having your last caffeinated drink earlier in the day.",
  alcohol: "Try leaving more time between an evening drink and bedtime.",
  bedroom: "Try making your bedroom darker, quieter and comfortably cool, for example with curtains, a fan or earplugs.",
  daylight: "Try getting some daylight and movement during the day, like a walk outside.",
  naps: "If you nap, try keeping naps short and earlier in the day.",
};

export function tipsFor(answers) {
  const keys = [];
  for (const q of SLEEP_QUESTIONS) {
    const i = answers[q.id];
    const tip = i === undefined ? null : q.options[i].tip;
    if (tip && !keys.includes(tip)) keys.push(tip);
  }
  return keys.map((k) => SLEEP_TIPS[k]);
}
