<script lang="ts">
	const API = '/api/trading/history';

	interface Trade {
		time: string;
		code: string;
		name?: string;
		qty: number;
		price?: number;
		tr_id: string;
		status: string;
		success: boolean;
		latency_ms?: number;
	}

	let trades = $state<Trade[]>([]);
	let loading = $state(true);
	let selectedDate = $state(new Date().toISOString().slice(0, 10));

	async function load() {
		loading = true;
		try {
			const res = await fetch(`${API}?date=${selectedDate}`);
			const json = await res.json();
			trades = json.trades ?? [];
		} catch {
			trades = [];
		}
		loading = false;
	}

	$effect(() => { selectedDate; load(); });

	const isBuy = (tr: string) => tr.includes('0802') || tr.toLowerCase().includes('buy');
	const typeLabel = (tr: string) => isBuy(tr) ? '매수' : '매도';
	const typeClass = (tr: string) => isBuy(tr) ? 'buy' : 'sell';
</script>

<div class="th-wrap">
	<div class="th-toolbar">
		<input type="date" bind:value={selectedDate} class="th-date" />
		<span class="th-count">{trades.length}건</span>
	</div>

	{#if loading}
		<p class="th-msg">불러오는 중...</p>
	{:else if trades.length === 0}
		<p class="th-msg">체결 내역이 없습니다</p>
	{:else}
		<table class="th-table">
			<thead>
				<tr>
					<th>시간</th>
					<th>구분</th>
					<th>종목</th>
					<th class="r">수량</th>
					<th class="r">상태</th>
				</tr>
			</thead>
			<tbody>
				{#each trades as t}
					<tr>
						<td class="mono">{t.time?.slice(11, 19) ?? '-'}</td>
						<td>
							<span class="th-type {typeClass(t.tr_id)}">{typeLabel(t.tr_id)}</span>
						</td>
						<td>
							<span class="th-name">{t.name ?? t.code}</span>
							<span class="th-code">{t.code}</span>
						</td>
						<td class="r mono">{t.qty}</td>
						<td class="r">
							<span class="th-status" class:ok={t.success} class:fail={!t.success}>
								{t.success ? '체결' : '실패'}
							</span>
						</td>
					</tr>
				{/each}
			</tbody>
		</table>
	{/if}
</div>

<style>
	.th-wrap { display: flex; flex-direction: column; gap: 0.625rem; }
	.th-toolbar { display: flex; align-items: center; gap: 0.5rem; }
	.th-date { padding: 0.3125rem 0.5rem; border: 1px solid #e5e7eb; border-radius: 0.25rem; font-size: 0.75rem; color: #374151; }
	.th-count { font-size: 0.6875rem; color: #9ca3af; font-weight: 600; }
	.th-msg { text-align: center; color: #9ca3af; font-size: 0.8125rem; padding: 2rem 0; }

	.th-table { width: 100%; border-collapse: collapse; font-size: 0.75rem; }
	.th-table th { padding: 0.375rem 0.5rem; font-size: 0.625rem; font-weight: 700; color: #9ca3af; border-bottom: 1px solid #e5e7eb; text-align: left; letter-spacing: 0.04em; }
	.th-table td { padding: 0.4375rem 0.5rem; border-bottom: 1px solid #f3f4f6; color: #374151; }
	.th-table .r { text-align: right; }
	.th-table .mono { font-family: 'SF Mono', 'Consolas', monospace; }

	.th-type { padding: 0.125rem 0.375rem; border-radius: 999px; font-size: 0.625rem; font-weight: 700; }
	.th-type.buy { color: var(--color-up); background: rgba(239, 68, 68, 0.08); }
	.th-type.sell { color: var(--color-down); background: rgba(59, 130, 246, 0.08); }

	.th-name { font-weight: 600; color: #111827; }
	.th-code { margin-left: 0.25rem; font-size: 0.625rem; color: #9ca3af; font-family: 'SF Mono', 'Consolas', monospace; }

	.th-status { font-size: 0.625rem; font-weight: 700; padding: 0.125rem 0.375rem; border-radius: 999px; }
	.th-status.ok { color: #059669; background: rgba(5, 150, 105, 0.08); }
	.th-status.fail { color: #dc2626; background: rgba(220, 38, 38, 0.08); }
</style>
