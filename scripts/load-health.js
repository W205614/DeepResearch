import http from 'k6/http';
import { check, sleep } from 'k6';
export const options = { vus: Number(__ENV.VUS || 10), duration: __ENV.DURATION || '30s', thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<500'] } };
export default function () { const r = http.get(`${__ENV.BASE_URL || 'http://host.docker.internal:8080'}/healthz`); check(r, { 'health 200': x => x.status === 200 }); sleep(0.2); }