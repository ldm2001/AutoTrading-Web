<script lang="ts">
	// 섹터 히트맵 — 포트폴리오 섹터별 비중/수익률 트리맵
	import { heatmapData, heatmapLoading, heatmapError, fetchHeatmap } from '$lib/stores/trading';
	import type { SectorCell } from '$lib/types';
	import './SectorHeatmap.css';

	let tooltip = $state<{ x: number; y: number; cell: SectorCell } | null>(null);
	let fetched = $state(false); // 무한 fetch 방지 플래그

	// 최초 1회만 fetch — 실패해도 재시도 안 함
	$effect(() => {
		if (!fetched) {
			fetched = true;
			fetchHeatmap();
		}
	});

	// 행 기반 트리맵 — 비중 합 35% 초과 시 줄바꿈
	const rows = $derived.by(() => {
		const sorted = [...$heatmapData].sort((a, b) => b.weight_pct - a.weight_pct);
		const result: SectorCell[][] = [];
		let current: SectorCell[] = [];
		let rowWeight = 0;
		for (const cell of sorted) {
			current.push(cell);
			rowWeight += cell.weight_pct;
			if (rowWeight >= 35) {
				result.push(current);
				current = [];
				rowWeight = 0;
			}
		}
		if (current.length) result.push(current);
		return result;
	});

	const totalSectors = $derived($heatmapData.length);
	const totalStocks = $derived($heatmapData.reduce((sum, s) => sum + s.stocks.length, 0));

	// 수익률→색상 변환 (빨강=수익, 파랑=손실)
	function cellColor(ret: number): string {
		if (ret > 0) {
			const intensity = Math.min(ret / 5, 1);
			return `rgba(239, 68, 68, ${0.3 + intensity * 0.7})`;
		}
		if (ret < 0) {
			const intensity = Math.min(Math.abs(ret) / 5, 1);
			return `rgba(59, 130, 246, ${0.3 + intensity * 0.7})`;
		}
		return '#6b7280';
	}

	function showTooltip(e: MouseEvent, cell: SectorCell) {
		tooltip = { x: e.clientX + 12, y: e.clientY + 12, cell };
	}

	function hideTooltip() {
		tooltip = null;
	}

	function retry() {
		fetched = false;
	}
</script>

<div class="heatmap-panel">
	{#if $heatmapLoading}
		<div class="heatmap-loading">
			<div class="heatmap-spinner"></div>
			<span>포트폴리오 로딩 중</span>
		</div>
	{:else if $heatmapError}
		<div class="heatmap-empty">
			<div>{$heatmapError}</div>
			<button class="btn-retry" onclick={retry}>다시 시도</button>
		</div>
	{:else if $heatmapData.length === 0}
		<div class="heatmap-empty">보유 종목이 없습니다.</div>
	{:else}
		<div class="heatmap-summary">
			<span>섹터 <strong>{totalSectors}</strong>개</span>
			<span>종목 <strong>{totalStocks}</strong>개</span>
		</div>

		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="heatmap-container">
			{#each rows as row}
				<div class="heatmap-row">
					{#each row as cell}
						<!-- svelte-ignore a11y_no_static_element_interactions -->
						<div
							class="heatmap-cell"
							style="flex-grow: {cell.weight_pct}; background-color: {cellColor(cell.avg_return)}"
							onmouseenter={(e) => showTooltip(e, cell)}
							onmouseleave={hideTooltip}
						>
							<span class="heatmap-sector">{cell.sector}</span>
							<span class="heatmap-weight">{cell.weight_pct.toFixed(1)}%</span>
							<span class="heatmap-return">
								{cell.avg_return > 0 ? '+' : ''}{cell.avg_return.toFixed(2)}%
							</span>
						</div>
					{/each}
				</div>
			{/each}
		</div>
	{/if}
</div>

{#if tooltip}
	<div class="heatmap-tooltip" style="left: {tooltip.x}px; top: {tooltip.y}px;">
		<div><strong>{tooltip.cell.sector}</strong> ({tooltip.cell.stocks.length}종목)</div>
		{#each tooltip.cell.stocks as stock}
			<div class="tooltip-stock">
				<span class="tooltip-name">{stock.name}</span>
				<span class="tooltip-pnl" class:up={stock.profit_loss_pct > 0} class:down={stock.profit_loss_pct < 0}>
					{stock.profit_loss_pct > 0 ? '+' : ''}{stock.profit_loss_pct.toFixed(2)}%
				</span>
			</div>
		{/each}
	</div>
{/if}
