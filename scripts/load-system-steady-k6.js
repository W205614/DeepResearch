// Isolated dr-perf stack only. Uses disposable OIDC tokens and fixed Agent fixtures.
import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

const tokens = JSON.parse(open('/load/tokens.json'));
const target = __ENV.PERFORMANCE_URL || 'http://edge';
if (target !== 'http://edge' || tokens.length < 32) throw new Error('Use the isolated edge and at least 32 fixture users');
const rates = {qps200: 200, qps250: 250, qps300: 300};
const stages = Object.keys(rates);
const ok = Object.fromEntries(stages.map(stage => [stage, new Counter(`steady_ok_${stage}`)]));
const limited = Object.fromEntries(stages.map(stage => [stage, new Counter(`steady_limited_${stage}`)]));
const other = Object.fromEntries(stages.map(stage => [stage, new Counter(`steady_other_${stage}`)]));
const latency = Object.fromEntries(stages.map(stage => [stage, new Trend(`steady_ok_latency_${stage}`, true)]));

function scenario(rate, startTime, execName) {
  return {executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: '30s', startTime,
    preAllocatedVUs: 150, maxVUs: 512, exec: execName};
}
export const options = {scenarios: {
  qps200: scenario(200, '0s', 'run200'),
  qps250: scenario(250, '32s', 'run250'),
  qps300: scenario(300, '64s', 'run300'),
}};

function hit(stage) {
  const token = tokens[exec.scenario.iterationInTest % tokens.length];
  const response = http.get(`${target}/api/threads`, {headers: {Authorization: `Bearer ${token}`}});
  if (response.status === 200) {
    ok[stage].add(1);
    latency[stage].add(response.timings.duration);
  } else if (response.status === 429 || response.status === 503) limited[stage].add(1);
  else other[stage].add(1);
}
export function run200() { hit('qps200'); }
export function run250() { hit('qps250'); }
export function run300() { hit('qps300'); }

export function handleSummary(data) {
  const result = {scope: 'isolated Docker, 64+ disposable OIDC users, GET /api/threads, 30s stages',
    dropped_iterations: data.metrics.dropped_iterations?.values?.count || 0, stages: {}};
  for (const stage of stages) {
    const success = data.metrics[`steady_ok_${stage}`]?.values?.count || 0;
    const shed = data.metrics[`steady_limited_${stage}`]?.values?.count || 0;
    const errors = data.metrics[`steady_other_${stage}`]?.values?.count || 0;
    const timing = data.metrics[`steady_ok_latency_${stage}`]?.values || {};
    result.stages[stage] = {target_qps: rates[stage], offered: rates[stage] * 30,
      executed: success + shed + errors, status_200: success, shed_429_or_503: shed,
      other_status: errors, admitted_p95_ms: timing['p(95)'] || 0};
  }
  const output = JSON.stringify(result);
  return {'/load/k6-steady-summary.json': output, stdout: output + '\n'};
}
