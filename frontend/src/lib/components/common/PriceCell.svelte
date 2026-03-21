<script lang="ts">
	let { price, change = 0, changePct = 0, size = 'md' }: {
		price: number;
		change?: number;
		changePct?: number;
		size?: 'sm' | 'md' | 'lg';
	} = $props();

	const direction = $derived(change > 0 ? 'up' : change < 0 ? 'down' : 'flat');
	const fmt = (n: number) => n.toLocaleString('ko-KR');
</script>

<span class="price-cell {size} {direction}">
	<span class="price-val">{fmt(price)}</span>
	{#if change !== 0}
		<span class="price-change">
			{change > 0 ? '▲' : '▼'}{fmt(Math.abs(change))}
			({changePct > 0 ? '+' : ''}{changePct.toFixed(2)}%)
		</span>
	{/if}
</span>

<style>
	.price-cell {
		display: inline-flex;
		align-items: baseline;
		gap: 0.375rem;
		font-family: var(--font-mono);
	}

	.price-val {
		font-weight: 600;
	}

	.price-change {
		font-weight: 500;
	}

	/* Direction */
	.up .price-val, .up .price-change { color: var(--color-up); }
	.down .price-val, .down .price-change { color: var(--color-down); }
	.flat .price-val { color: var(--color-text-secondary); }
	.flat .price-change { color: var(--color-text-hint); }

	/* Sizes */
	.sm .price-val { font-size: 0.75rem; }
	.sm .price-change { font-size: 0.625rem; }

	.md .price-val { font-size: 0.875rem; }
	.md .price-change { font-size: 0.75rem; }

	.lg .price-val { font-size: 1.125rem; }
	.lg .price-change { font-size: 0.875rem; }
</style>
