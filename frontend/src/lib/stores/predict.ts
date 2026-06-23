import { writable } from 'svelte/store';
import type { PredictionResult } from '$lib/types';

// 5일 예측 결과 스토어
export const prediction = writable<PredictionResult | null>(null);
// 예측 로딩 상태
export const predictionLoading = writable(false);

// 종목 Transformer 예측 조회
export async function predq(code: string): Promise<void> {
	predictionLoading.set(true);
	prediction.set(null);
	try {
		const resp = await fetch(`/api/predict/${code}`);
		if (resp.ok) {
			prediction.set(await resp.json());
		}
	} catch {
		// 네트워크 오류 무시
	} finally {
		predictionLoading.set(false);
	}
}
