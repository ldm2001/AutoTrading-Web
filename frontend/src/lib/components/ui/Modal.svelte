<script lang="ts">
	// 모달 다이얼로그 — 배경 클릭·ESC로 닫기
	import type { Snippet } from 'svelte';
	import './Modal.css';

	interface Props {
		open: boolean;
		title: string;
		onclose: () => void;
		children: Snippet;
	}

	let { open, title, onclose, children }: Props = $props();

	// 백드롭 클릭 시 닫기
	function bg(e: MouseEvent) {
		if (e.target === e.currentTarget) onclose();
	}

	// ESC 키로 닫기
	function key(e: KeyboardEvent) {
		if (e.key === 'Escape') onclose();
	}
</script>

<svelte:window onkeydown={key} />

{#if open}
	<div class="modal-backdrop" onclick={bg} onkeydown={key} role="dialog" aria-modal="true" tabindex="-1">
		<div class="modal-container">
			<div class="modal-header">
				<span class="modal-title">{title}</span>
				<button class="modal-close" onclick={onclose}>&times;</button>
			</div>
			<div class="modal-body">
				{@render children()}
			</div>
		</div>
	</div>
{/if}
