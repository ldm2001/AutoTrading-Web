// 토스트 알림 스토어
import { writable } from 'svelte/store';

export interface Toast {
	id: number;
	type: 'success' | 'error' | 'info';
	message: string;
}

let nextId = 0;
export const toasts = writable<Toast[]>([]);

export function toast(message: string, type: Toast['type'] = 'info', duration = 3000) {
	const id = nextId++;
	toasts.update(list => [...list, { id, type, message }]);
	setTimeout(() => {
		toasts.update(list => list.filter(t => t.id !== id));
	}, duration);
}

export function toastSuccess(message: string) { toast(message, 'success'); }
export function toastError(message: string) { toast(message, 'error', 5000); }
