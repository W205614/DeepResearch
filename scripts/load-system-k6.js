import http from 'k6/http';
import exec from 'k6/execution';
import { Counter, Trend } from 'k6/metrics';

const tokens = JSON.parse(open('/load/tokens.json'));
const target = __ENV.PERFORMANCE_URL || 'http://edge';
const stages = ['qps100', 'qps250', 'qps500', 'qps800', 'qps1200'];
const rates = {qps100: 100, qps250: 250, qps500: 500, qps800: 800, qps1200: 1200};
const latency = Object.fromEntries(stages.map(name => [name, new Trend(`latency_${name}`, true)]));
const ok = Object.fromEntries(stages.map(name => [name, new Counter(`ok_${name}`)]));
const limited = Object.fromEntries(stages.map(name => [name, new Counter(`limited_${name}`)]));
const failed = Object.fromEntries(stages.map(name => [name, new Counter(`failed_${name}`)]));

function scenario(rate, startTime, execName) {
  return {executor: 'constant-arrival-rate', rate, timeUnit: '1s', duration: '3s', startTime,
    preAllocatedVUs: Math.max(50, Math.ceil(rate / 2)), maxVUs: Math.max(200, rate * 2), exec: execName};
}

export const options = {scenarios: {
  qps100: scenario(100, '0s', 'run100'),
  qps250: scenario(250, '4s', 'run250'),
  qps500: scenario(500, '8s', 'run500'),
  qps800: scenario(800, '12s', 'run800'),
  qps1200: scenario(1200, '16s', 'run1200'),
}};

function hit(stage) {
  const token = tokens[exec.scenario.iterationInTest % tokens.length];
  const response = http.get(`${target}/api/threads`, {headers: {Authorization: `Bearer ${token}`}});
  latency[stage].add(response.timings.duration);
  if (response.status === 200) ok[stage].add(1);
  else if (response.status === 429 || response.status === 503) limited[stage].add(1);
  else failed[stage].add(1);
}

export function run100() { hit('qps100'); }
export function run250() { hit('qps250'); }
export function run500() { hit('qps500'); }
export function run800() { hit('qps800'); }
export function run1200() { hit('qps1200'); }

export function handleSummary(data) {
  const result = {};
  for (const stage of stages) {
    const values = data.metrics[`latency_${stage}`]?.values || {};
    const success = data.metrics[`ok_${stage}`]?.values?.count || 0;
    const shed = data.metrics[`limited_${stage}`]?.values?.count || 0;
    const errors = data.metrics[`failed_${stage}`]?.values?.count || 0;
    result[stage] = {target_qps: rates[stage], offered: rates[stage] * 3,
      executed: success + shed + errors, status_200: success, shed_429_or_503: shed,
      other_errors: errors, p95_ms: values['p(95)'] || 0, max_ms: values.max || 0};
  }
  result.dropped_iterations = data.metrics.dropped_iterations?.values?.count || 0;
  const output = JSON.stringify(result);
  return {'/load/k6-summary.json': output, stdout: output + '\n'};
}
