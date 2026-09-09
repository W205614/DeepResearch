import http from 'k6/http';
import { check, sleep } from 'k6';

const token = __ENV.AUTHORIZATION;
if (!token) throw new Error('Set AUTHORIZATION to an OIDC Bearer token; the script will not load an anonymous endpoint.');
export const options = {
  vus: Number(__ENV.VUS || 10), duration: __ENV.DURATION || '5m',
  thresholds: { http_req_failed: ['rate<0.01'], http_req_duration: ['p(95)<2000'] },
};
export default function () {
  const response = http.get(`${__ENV.BASE_URL || 'http://host.docker.internal:8080'}/api/metrics`, {headers: {Authorization: token}});
  check(response, {'authenticated metrics 200': item => item.status === 200});
  sleep(0.2);
}
