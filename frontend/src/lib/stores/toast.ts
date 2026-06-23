// 토스트 알림 스토어
import { writable } from 'svelte/store';

// 토스트 항목 타입
export interface Toast {
	id: number;
	type: 'success' | 'error' | 'info';
	message: string;
}

let nextId = 0;
// 활성 토스트 목록
export const toasts = writable<Toast[]>([]);

// 토스트 추가 + duration 후 자동 제거
export function pop(message: string, type: Toast['type'] = 'info', duration = 3000) {
	const id = nextId++;
	toasts.update(list => [...list, { id, type, message }]);
	setTimeout(() => {
		toasts.update(list => list.filter(t => t.id !== id));
	}, duration);
}

// 성공 토스트
export function ok(message: string) { pop(message, 'success'); }
// 에러 토스트 (5초 유지)
export function bad(message: string) { pop(message, 'error', 5000); }
