<script lang="ts">
	const API_PORTFOLIO = '/api/trading/portfolio';
	const API_HISTORY   = '/api/trading/history';

	interface Holding {
		name: string;
		code: string;
		profit_loss: number;
		profit_loss_percent: number;
		eval_amount: number;
	}

	let holdings = $state<Holding[]>([]);
	let totalPL = $state(0);
	let totalEval = $state(0);
	let cash = $state(0);
	let todayTrades = $state(0);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			const [portRes, histRes] = await Promise.all([
				fetch(API_PORTFOLIO),
				fetch(API_HISTORY),
			]);
			const port = await portRes.json();
			const hist = await histRes.json();

			holdings  = (port.items ?? []).sort((a: Holding, b: Holding) => b.profit_loss - a.profit_loss);
			totalPL   = port.total_profit_loss ?? 0;
			totalEval = port.total_eval ?? 0;
			cash      = port.cash_balance ?? 0;
			todayTrades = (hist.trades ?? []).length;
		} catch {
			holdings = [];
		}
		loading = false;
	}

	$effect(() => { load(); });

	const totalReturn = $derived(totalEval > 0 ? ((totalPL / (totalEval - totalPL)) * 100) : 0);
	const plClass = (v: number) => v > 0 ? 'up' : v < 0 ? 'down' : '';

	const winners = $derived(holdings.filter(h => h.profit_loss > 0));
	const losers  = $derived(holdings.filter(h => h.profit_loss < 0));
	const winRate = $derived(holdings.length > 0 ? Math.round(winners.length / holdings.length * 100) : 0);
</script>

<div class="pp-wrap">
	{#if loading}
		<p class="pp-msg">불러오는 중...</p>
	{:else}
		<div class="pp-hero">
			<div class="pp-hero-main">
				<span class="pp-hero-label">총 평가손익</span>
				<span class="pp-hero-value {plClass(totalPL)}">
					{totalPL > 0 ? '+' : ''}{totalPL.toLocaleString()}원
				</span>
				<span class="pp-hero-pct {plClass(totalReturn)}">
					({totalReturn > 0 ? '+' : ''}{totalReturn.toFixed(2)}%)
				</span>
			</div>
		</div>

		<div class="pp-stats">
			<div class="pp-stat">
				<span class="pp-stat-label">총 자산</span>
				<strong>{(totalEval + cash).toLocaleString()}원</strong>
			</div>
			<div class="pp-stat">
				<span class="pp-stat-label">보유 종목</span>
				<strong>{holdings.length}개</strong>
			</div>
			<div class="pp-stat">
				<span class="pp-stat-label">승률</span>
				<strong>{winRate}%</strong>
			</div>
			<div class="pp-stat">
				<span class="pp-stat-label">오늘 체결</span>
				<strong>{todayTrades}건</strong>
			</div>
		</div>

		{#if holdings.length > 0}
			<div class="pp-section">
				<span class="pp-section-title">종목별 손익</span>
				<div class="pp-bars">
					{#each holdings as h}
						{@const maxAbs = Math.max(...holdings.map(x => Math.abs(x.profit_loss_percent)), 1)}
						{@const width = Math.min(Math.abs(h.profit_loss_percent) / maxAbs * 100, 100)}
						<div class="pp-bar-row">
							<span class="pp-bar-name">{h.name}</span>
							<div class="pp-bar-track">
								<div
									class="pp-bar-fill {plClass(h.profit_loss_percent)}"
									style="width: {width}%"
								></div>
							</div>
							<span class="pp-bar-value {plClass(h.profit_loss_percent)}">
								{h.profit_loss_percent > 0 ? '+' : ''}{h.profit_loss_percent.toFixed(2)}%
							</span>
						</div>
					{/each}
				</div>
			</div>
		{:else}
			<p class="pp-msg">보유종목이 없습니다</p>
		{/if}
	{/if}
</div>

<style>
	.pp-wrap { display: flex; flex-direction: column; gap: 0.875rem; }
	.pp-msg { text-align: center; color: #9ca3af; font-size: 0.8125rem; padding: 2rem 0; }

	.pp-hero { text-align: center; padding: 1rem 0; }
	.pp-hero-label { display: block; font-size: 0.625rem; font-weight: 700; color: #9ca3af; letter-spacing: 0.06em; margin-bottom: 0.25rem; }
	.pp-hero-value { font-size: 1.5rem; font-weight: 800; font-family: 'SF Mono', 'Consolas', monospace; color: #111827; }
	.pp-hero-pct { font-size: 0.875rem; font-weight: 600; font-family: 'SF Mono', 'Consolas', monospace; margin-left: 0.375rem; }

	.pp-stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.5rem; }
	.pp-stat { display: flex; flex-direction: column; gap: 0.125rem; padding: 0.5rem 0.625rem; border: 1px solid #e5e7eb; border-radius: 0.375rem; background: #f9fafb; text-align: center; }
	.pp-stat-label { font-size: 0.5625rem; font-weight: 700; color: #9ca3af; letter-spacing: 0.04em; }
	.pp-stat strong { font-size: 0.8125rem; font-weight: 700; color: #111827; font-family: 'SF Mono', 'Consolas', monospace; }

	.pp-section-title { font-size: 0.6875rem; font-weight: 700; color: #374151; }
	.pp-section { display: flex; flex-direction: column; gap: 0.5rem; }

	.pp-bars { display: flex; flex-direction: column; gap: 0.375rem; }
	.pp-bar-row { display: grid; grid-template-columns: 5rem 1fr 4rem; align-items: center; gap: 0.5rem; }
	.pp-bar-name { font-size: 0.6875rem; font-weight: 600; color: #374151; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.pp-bar-track { height: 0.5rem; background: #f3f4f6; border-radius: 999px; overflow: hidden; }
	.pp-bar-fill { height: 100%; border-radius: 999px; transition: width 0.3s; }
	.pp-bar-fill.up { background: var(--color-up); }
	.pp-bar-fill.down { background: var(--color-down); }
	.pp-bar-value { font-size: 0.6875rem; font-weight: 700; font-family: 'SF Mono', 'Consolas', monospace; text-align: right; }

	.up { color: var(--color-up); }
	.down { color: var(--color-down); }

	@media (max-width: 640px) { .pp-stats { grid-template-columns: repeat(2, 1fr); } }
</style>
