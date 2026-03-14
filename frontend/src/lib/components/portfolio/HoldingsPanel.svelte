<script lang="ts">
	const API = '/api/trading/portfolio';

	interface Holding {
		code: string;
		name: string;
		qty: number;
		avg_price: number;
		current_price: number;
		eval_amount: number;
		profit_loss: number;
		profit_loss_percent: number;
	}

	interface Portfolio {
		items: Holding[];
		total_eval: number;
		total_profit_loss: number;
		cash_balance: number;
	}

	let data = $state<Portfolio | null>(null);
	let loading = $state(true);

	async function load() {
		loading = true;
		try {
			const res = await fetch(API);
			data = await res.json();
		} catch {
			data = null;
		}
		loading = false;
	}

	$effect(() => { load(); });

	const totalAsset = $derived(data ? data.total_eval + data.cash_balance : 0);
	const plClass = (v: number) => v > 0 ? 'up' : v < 0 ? 'down' : '';
</script>

<div class="hp-wrap">
	{#if loading}
		<p class="hp-loading">불러오는 중...</p>
	{:else if !data || data.items.length === 0}
		<p class="hp-empty">보유종목이 없습니다</p>
	{:else}
		<div class="hp-summary">
			<div class="hp-card">
				<span class="hp-label">총 평가금액</span>
				<strong>{data.total_eval.toLocaleString()}원</strong>
			</div>
			<div class="hp-card">
				<span class="hp-label">총 손익</span>
				<strong class={plClass(data.total_profit_loss)}>
					{data.total_profit_loss > 0 ? '+' : ''}{data.total_profit_loss.toLocaleString()}원
				</strong>
			</div>
			<div class="hp-card">
				<span class="hp-label">예수금</span>
				<strong>{data.cash_balance.toLocaleString()}원</strong>
			</div>
			<div class="hp-card">
				<span class="hp-label">총 자산</span>
				<strong>{totalAsset.toLocaleString()}원</strong>
			</div>
		</div>

		<table class="hp-table">
			<thead>
				<tr>
					<th>종목</th>
					<th class="r">수량</th>
					<th class="r">평단가</th>
					<th class="r">현재가</th>
					<th class="r">평가금액</th>
					<th class="r">손익</th>
					<th class="r">수익률</th>
				</tr>
			</thead>
			<tbody>
				{#each data.items as h}
					<tr>
						<td>
							<span class="hp-name">{h.name}</span>
							<span class="hp-code">{h.code}</span>
						</td>
						<td class="r">{h.qty}</td>
						<td class="r mono">{h.avg_price.toLocaleString()}</td>
						<td class="r mono">{h.current_price.toLocaleString()}</td>
						<td class="r mono">{h.eval_amount.toLocaleString()}</td>
						<td class="r mono {plClass(h.profit_loss)}">
							{h.profit_loss > 0 ? '+' : ''}{h.profit_loss.toLocaleString()}
						</td>
						<td class="r mono {plClass(h.profit_loss_percent)}">
							{h.profit_loss_percent > 0 ? '+' : ''}{h.profit_loss_percent.toFixed(2)}%
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>

<style>
	.hp-wrap { display: flex; flex-direction: column; gap: 0.75rem; }
	.hp-loading, .hp-empty { text-align: center; color: #9ca3af; font-size: 0.8125rem; padding: 2rem 0; }

	.hp-summary { display: grid; grid-template-columns: repeat(4, 1fr); gap: 0.5rem; }
	.hp-card { display: flex; flex-direction: column; gap: 0.125rem; padding: 0.625rem 0.75rem; border: 1px solid #e5e7eb; border-radius: 0.375rem; background: #f9fafb; }
	.hp-card .hp-label { font-size: 0.625rem; font-weight: 700; color: #9ca3af; letter-spacing: 0.04em; }
	.hp-card strong { font-size: 0.875rem; font-weight: 700; color: #111827; font-family: 'SF Mono', 'Consolas', monospace; }

	.hp-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; }
	.hp-table th { padding: 0.375rem 0.5rem; font-size: 0.625rem; font-weight: 700; color: #9ca3af; border-bottom: 1px solid #e5e7eb; text-align: left; letter-spacing: 0.04em; }
	.hp-table td { padding: 0.4375rem 0.5rem; border-bottom: 1px solid #f3f4f6; color: #374151; }
	.hp-table .r { text-align: right; }
	.hp-table .mono { font-family: 'SF Mono', 'Consolas', monospace; }

	.hp-name { font-weight: 600; color: #111827; }
	.hp-code { margin-left: 0.25rem; font-size: 0.625rem; color: #9ca3af; font-family: 'SF Mono', 'Consolas', monospace; }

	:global(.up) { color: var(--color-up) !important; }
	:global(.down) { color: var(--color-down) !important; }

	@media (max-width: 640px) { .hp-summary { grid-template-columns: repeat(2, 1fr); } }
</style>
