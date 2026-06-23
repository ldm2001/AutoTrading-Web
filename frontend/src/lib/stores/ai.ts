import { writable } from 'svelte/store';
import type { AISignal, NewsSentiment } from '$lib/types';

// AI 시그널 스토어
export const aiSignal = writable<AISignal | null>(null);
// 뉴스 감성 스토어
export const newsSentiment = writable<NewsSentiment | null>(null);
// 당일 마켓 리포트 스토어
export const dailyReport = writable<string | null>(null);
// AI 분석 로딩 상태
export const aiLoading = writable<boolean>(false);

// AI 시그널 조회 (실패 시 지표만 폴백)
export async function aisig(code: string) {
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

// 종목 뉴스 감성 조회
export async function moodq(code: string) {
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

// 당일 마켓 리포트 조회
export async function rptq() {
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
