<script lang="ts">
	import { toasts } from '$lib/stores/toast';
	import { fly } from 'svelte/transition';
</script>

<div class="toast-container">
	{#each $toasts as t (t.id)}
		<div
			class="toast-item {t.type}"
			transition:fly={{ y: 20, duration: 200 }}
		>
			<span class="toast-icon">
				{#if t.type === 'success'}✓
				{:else if t.type === 'error'}✕
				{:else}ℹ
				{/if}
			</span>
			<span class="toast-msg">{t.message}</span>
		</div>
	{/each}
</div>

<style>
	.toast-container {
		position: fixed;
		bottom: 1.5rem;
		right: 1.5rem;
		z-index: 9999;
		display: flex;
		flex-direction: column;
		gap: 0.5rem;
		pointer-events: none;
	}

	.toast-item {
		display: flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0.625rem 1rem;
		border-radius: 0.375rem;
		font-size: 0.8125rem;
		font-weight: 500;
		box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
		pointer-events: auto;
		max-width: 20rem;
	}

	.toast-item.success {
		background: #ecfdf5;
		color: #065f46;
		border: 1px solid #a7f3d0;
	}

	.toast-item.error {
		background: #fef2f2;
		color: #991b1b;
		border: 1px solid #fecaca;
	}

	.toast-item.info {
		background: #eff6ff;
		color: #1e40af;
		border: 1px solid #bfdbfe;
	}

	.toast-icon {
		font-weight: 700;
		font-size: 0.875rem;
		flex-shrink: 0;
	}

	.toast-msg {
		line-height: 1.4;
	}
</style>
