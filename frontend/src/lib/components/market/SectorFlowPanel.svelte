<script lang="ts">
	const API = '/api/stocks/sector/flow';

	interface SectorStock { name: string; change_pct: number; }
	interface Sector {
		sector: string;
		avg_change_pct: number;
		stock_count: number;
		top_stocks: SectorStock[];
		bottom_stocks: SectorStock[];
	}

	let sectors = $state<Sector[]>([]);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			const res = await fetch(API);
			sectors = await res.json();
		} catch {
			sectors = [];
		}
		loading = false;
	}

	$effect(() => { load(); });

	const plClass = (v: number) => v > 0 ? 'up' : v < 0 ? 'down' : '';
	const maxChange = $derived(Math.max(...sectors.map(s => Math.abs(s.avg_change_pct)), 1));
</script>

<div class="sf-wrap">
	{#if loading}
		<p class="sf-msg">업종 데이터 수집 중... (다소 시간이 걸릴 수 있습니다)</p>
	{:else if sectors.length === 0}
		<p class="sf-msg">데이터를 불러올 수 없습니다</p>
	{:else}
		<div class="sf-list">
			{#each sectors as s}
				{@const barWidth = Math.min(Math.abs(s.avg_change_pct) / maxChange * 100, 100)}
				<div class="sf-row">
					<div class="sf-header">
						<span class="sf-name">{s.sector}</span>
						<span class="sf-count">{s.stock_count}종목</span>
						<span class="sf-change {plClass(s.avg_change_pct)}">
							{s.avg_change_pct > 0 ? '+' : ''}{s.avg_change_pct.toFixed(2)}%
						</span>
					</div>
					<div class="sf-bar-track">
						<div
							class="sf-bar-fill {plClass(s.avg_change_pct)}"
							style="width: {barWidth}%"
						></div>
					</div>
					<div class="sf-stocks">
						{#each s.top_stocks as st}
							<span class="sf-stock {plClass(st.change_pct)}">
								{st.name} {st.change_pct > 0 ? '+' : ''}{st.change_pct.toFixed(1)}%
							</span>
						{/each}
						{#if s.bottom_stocks.length > 0 && s.bottom_stocks[0].change_pct < 0}
							<span class="sf-sep">|</span>
							{#each s.bottom_stocks as st}
								<span class="sf-stock {plClass(st.change_pct)}">
									{st.name} {st.change_pct.toFixed(1)}%
								</span>
							{/each}
						{/if}
					</div>
				</div>
			{/each}
		</div>
	{/if}
</div>

<style>
	.sf-wrap { display: flex; flex-direction: column; gap: 0.5rem; }
	.sf-msg { text-align: center; color: #9ca3af; font-size: 0.8125rem; padding: 2rem 0; }

	.sf-list { display: flex; flex-direction: column; gap: 0.625rem; }
	.sf-row { display: flex; flex-direction: column; gap: 0.25rem; padding: 0.5rem 0.625rem; border: 1px solid #f3f4f6; border-radius: 0.375rem; background: #fafafa; }

	.sf-header { display: flex; align-items: center; gap: 0.5rem; }
	.sf-name { font-size: 0.8125rem; font-weight: 700; color: #111827; }
	.sf-count { font-size: 0.5625rem; color: #9ca3af; font-weight: 600; }
	.sf-change { margin-left: auto; font-size: 0.8125rem; font-weight: 700; font-family: 'SF Mono', 'Consolas', monospace; }

	.sf-bar-track { height: 0.375rem; background: #e5e7eb; border-radius: 999px; overflow: hidden; }
	.sf-bar-fill { height: 100%; border-radius: 999px; transition: width 0.3s; }
	.sf-bar-fill.up { background: var(--color-up); }
	.sf-bar-fill.down { background: var(--color-down); }

	.sf-stocks { display: flex; flex-wrap: wrap; gap: 0.25rem; align-items: center; }
	.sf-stock { font-size: 0.5625rem; font-weight: 600; padding: 0.0625rem 0.3125rem; border-radius: 0.1875rem; background: #f3f4f6; }
	.sf-sep { color: #d1d5db; font-size: 0.5625rem; }

	.up { color: var(--color-up); }
	.down { color: var(--color-down); }
</style>
