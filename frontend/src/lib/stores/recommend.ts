import { writable } from 'svelte/store';
import type { RecommendStock } from '$lib/types';

export const recommendations = writable<RecommendStock[]>([]);
export const recommendLoading = writable(false);

export async function fetchRecommendations() {
	recommendLoading.set(true);
	try {
		const resp = await fetch('/api/stocks/recommend');
		if (resp.ok) {
			recommendations.set(await resp.json());
		}
	} catch {
		// ignore
	} finally {
		recommendLoading.set(false);
	}
}
