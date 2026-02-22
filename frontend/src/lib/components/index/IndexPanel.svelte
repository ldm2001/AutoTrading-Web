<script lang="ts">
	import { indices } from '$lib/stores/stocks';
	import './IndexPanel.css';

	function fmtPct(n: number): string {
		return `${n > 0 ? '+' : ''}${n.toFixed(2)}%`;
	}
</script>

<div class="index-grid">
	{#each $indices as idx (idx.code)}
		<div class="index-card">
			<div class="index-name">{idx.name}</div>
			<div class="index-value">{idx.value.toFixed(2)}</div>
			<div
				class="index-change"
				class:up={idx.change_percent > 0}
				class:down={idx.change_percent < 0}
				class:flat={idx.change_percent === 0}
			>
				{idx.change > 0 ? '+' : ''}{idx.change.toFixed(2)}
				({fmtPct(idx.change_percent)})
			</div>
		</div>
	{/each}

	{#if $indices.length === 0}
		{#each Array(4) as _}
			<div class="skeleton-card">
				<div class="skeleton-bar skeleton-sm"></div>
				<div class="skeleton-bar skeleton-md"></div>
				<div class="skeleton-bar skeleton-xs"></div>
			</div>
		{/each}
	{/if}
</div>
