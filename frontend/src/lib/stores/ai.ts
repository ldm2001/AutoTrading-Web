import { writable } from 'svelte/store';
import type { AISignal, NewsSentiment } from '$lib/types';

export const aiSignal = writable<AISignal | null>(null);
export const newsSentiment = writable<NewsSentiment | null>(null);
export const dailyReport = writable<string | null>(null);
export const aiLoading = writable<boolean>(false);

export async function fetchAISignal(code: string) {
	aiSignal.set(null);
	aiLoading.set(true);
	try {
		const resp = await fetch(`/api/ai/signal/${code}`);
		if (resp.ok) {
			aiSignal.set(await resp.json());
		} else {
			// signal API 실패 시 indicators만 가져오기
			const indResp = await fetch(`/api/ai/indicators/${code}`);
			if (indResp.ok) {
				const indicators = await indResp.json();
				aiSignal.set({
					code,
					name: '',
					signal: 'hold',
					confidence: 0,
					reasons: ['AI 분석 불가 (지표만 표시)'],
					indicators,
					news_count: 0,
				});
			} else {
				aiSignal.set(null);
			}
		}
	} catch {
		aiSignal.set(null);
	} finally {
		aiLoading.set(false);
	}
}

export async function fetchNewsSentiment(code: string) {
	try {
		const resp = await fetch(`/api/ai/news/${code}`);
		if (resp.ok) {
			newsSentiment.set(await resp.json());
		} else {
			newsSentiment.set(null);
		}
	} catch {
		newsSentiment.set(null);
	}
}

export async function fetchDailyReport() {
	try {
		const resp = await fetch('/api/ai/report');
		if (resp.ok) {
			const data = await resp.json();
			dailyReport.set(data.report || '');
		} else {
			dailyReport.set('');
		}
	} catch {
		dailyReport.set('');
	}
}
