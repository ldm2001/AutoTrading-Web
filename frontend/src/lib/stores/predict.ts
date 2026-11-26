import { writable } from 'svelte/store';
import type { PredictionResult } from '$lib/types';

// 5일 예측 결과 스토어
export const prediction = writable<PredictionResult | null>(null);
// 예측 로딩 상태
export const predictionLoading = writable(false);
// 예측 실패 문구 (429 포함) — 빈 화면과 구분
export const predictionError = writable<string | null>(null);

// 요청 순번 — 가장 최근 요청의 응답만 스토어에 반영
let seq = 0;

// 0% 예측 대비 개선율 표시 — skill(1 − mae/mae_zero)을 %로, 0 이하면 worse (기준선보다 나쁨), 없거나 유한하지 않으면 '-'
export function skillnote(skill: number | null | undefined): { text: string; worse: boolean } {
	if (typeof skill !== 'number' || !Number.isFinite(skill)) return { text: '-', worse: false };
	const pct = skill * 100;
	return { text: `${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`, worse: skill <= 0 };
}

// 실패 응답 문구 — 서버 detail이 있으면 덧붙임
async function reason(resp: Response): Promise<string> {
	if (resp.status === 429) return '요청이 많습니다. 잠시 후 다시 시도하세요';
	let detail = '';
	try {
		const body = await resp.json();
		if (typeof body?.detail === 'string') detail = body.detail;
	} catch {
		// JSON이 아닌 응답 (프록시 오류 페이지 등)
	}
	return detail ? `예측 실패 (${resp.status}): ${detail}` : `예측 실패 (${resp.status})`;
}

// 종목 Transformer 예측 조회 (그 사이 다른 종목을 골랐으면 늦게 온 응답은 버림)
export async function predq(code: string): Promise<void> {
	const my = ++seq;
	predictionLoading.set(true);
	predictionError.set(null);
	prediction.set(null);
	try {
		const resp = await fetch(`/api/predict/${code}`);
		if (resp.ok) {
			const result: PredictionResult = await resp.json();
			if (my === seq) prediction.set(result);
		} else {
			const message = await reason(resp);
			if (my === seq) predictionError.set(message);
		}
	} catch {
		if (my === seq) predictionError.set('서버 연결 실패');
	} finally {
		if (my === seq) predictionLoading.set(false);
	}
}
