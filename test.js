import http from 'k6/http';
import { sleep, check } from 'k6';

export const options = {
  stages: [
    { duration: '20s', target: 100 }, // 20초 만에 100명으로 급증 
    { duration: '1m',  target: 500 }, // 1분 동안 500명까지 밀어붙이기
    { duration: '20s', target: 0   }, // 20초 동안 종료
  ],
};

export default function () {
  // 서버 체크 경로
  const res = http.get('http://localhost:8000/api/health'); 

  check(res, {
    'is status 200': (r) => r.status === 200,
    'transaction time < 500ms': (r) => r.timings.duration < 500,
  });

  sleep(1);
}

