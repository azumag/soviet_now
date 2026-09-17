import { readFileSync } from 'node:fs';

// Same fixed manifest as the shell workers; no start/reload-relative timer.
const window = JSON.parse(readFileSync(new URL('../config/ai_priority_window.json', import.meta.url), 'utf8'));
const start = Date.parse(window.start_utc);
const end = Date.parse(window.end_utc);
if (end - start !== 7 * 86400000) throw new Error('invalid AI priority window');
export const priorityAgents = Object.freeze([...window.agents]);
export function priorityNow() {
  return process.env.AI_PRIORITY_NOW_EPOCH == null
    ? Date.now() : Number(process.env.AI_PRIORITY_NOW_EPOCH) * 1000;
}
export function priorityActive(now = priorityNow()) {
  return now >= start && now < end;
}
export function prependPriority(agents, now = priorityNow()) {
  if (!agents.length || !priorityActive(now)) return [...agents];
  return [...priorityAgents, ...agents.filter(agent => !priorityAgents.includes(agent))];
}
