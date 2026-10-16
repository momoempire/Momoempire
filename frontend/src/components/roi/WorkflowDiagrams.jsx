import { useId } from "react";
import { COPY, DIAGRAMS } from "../../config/roi";

// In-code SVG diagrams (no images). Colors come from currentColor + the theme classes
// (.text-white / .hvac-cool / .hvac-warm), so they follow #55's light and dark themes.
function wrap(text, max = 22) {
  const words = text.split(" ");
  const lines = [];
  let cur = "";
  for (const w of words) {
    if ((cur + " " + w).trim().length > max && cur) { lines.push(cur); cur = w; } else cur = (cur + " " + w).trim();
  }
  if (cur) lines.push(cur);
  return lines;
}

export function WorkflowDiagram({ diagram, index }) {
  const uid = useId();
  const tId = `${uid}-t`;
  const dId = `${uid}-d`;
  const W = 240, boxH = 58, gap = 30, top = 10;
  const H = top + diagram.steps.length * (boxH + gap) - gap + 10;
  const tone = index === 2 ? "hvac-warm" : "hvac-cool";
  return (
    <figure className="glass-crystal rounded-2xl p-5 text-white" data-testid={`diagram-${diagram.id}`}>
      <figcaption className="font-display text-lg mb-3">{diagram.title}</figcaption>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-labelledby={`${tId} ${dId}`} className="max-w-xs mx-auto block">
        <title id={tId}>{diagram.title}</title>
        <desc id={dId}>{diagram.steps.map((s, i) => `${i + 1}. ${s}`).join(" → ")}</desc>
        {diagram.steps.map((s, i) => {
          const y = top + i * (boxH + gap);
          const lines = wrap(s);
          return (
            <g key={s}>
              <g className={tone}>
                <rect x="10" y={y} width={W - 20} height={boxH} rx="12" fill="currentColor" fillOpacity="0.08" stroke="currentColor" strokeWidth="1.5" />
                <circle cx="34" cy={y + boxH / 2} r="12" fill="currentColor" />
              </g>
              <text x="34" y={y + boxH / 2 + 4} textAnchor="middle" fontSize="12" fontWeight="700" className="text-black" fill="currentColor">{i + 1}</text>
              <text className="text-white" fill="currentColor" fontSize="13" x="56" y={y + boxH / 2 - (lines.length - 1) * 8 + 4}>
                {lines.map((l, j) => <tspan key={l} x="56" dy={j ? 16 : 0}>{l}</tspan>)}
              </text>
              {i < diagram.steps.length - 1 && (
                <g className={tone} stroke="currentColor" strokeWidth="1.5" fill="none">
                  <line x1={W / 2} y1={y + boxH + 3} x2={W / 2} y2={y + boxH + gap - 5} />
                  <polyline points={`${W / 2 - 5},${y + boxH + gap - 10} ${W / 2},${y + boxH + gap - 4} ${W / 2 + 5},${y + boxH + gap - 10}`} />
                </g>
              )}
            </g>
          );
        })}
      </svg>
    </figure>
  );
}

export default function WorkflowDiagrams({ lang = "en" }) {
  const c = COPY[lang] || COPY.en;
  const list = DIAGRAMS[lang] || DIAGRAMS.en;
  return (
    <section aria-label={c.diagramsTitle} data-testid="workflow-diagrams">
      <h2 className="font-display text-2xl text-white">{c.diagramsTitle}</h2>
      <p className="mt-2 text-sm text-white/70">{c.diagramsNote}</p>
      <div className="mt-5 grid grid-cols-1 md:grid-cols-3 gap-4">
        {list.map((d, i) => <WorkflowDiagram key={d.id} diagram={d} index={i} />)}
      </div>
    </section>
  );
}
