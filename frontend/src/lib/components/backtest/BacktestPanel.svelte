<script lang="ts">
	// 백테스트 패널 — 파라미터 입력 + 결과 표시
	import { selectedStock, selectedStockDetail } from '$lib/stores/stocks';
	import './BacktestPanel.css';

	// 개별 거래 기록 타입
	interface BacktestTrade {
		entry_bar: number;
		entry_time: string;
		entry_price: number;
		exit_bar: number;
		exit_time: string;
		exit_price: number;
		exit_reason: string;
		pnl_pct: number;
	}

	// 백테스트 결과 타입
	interface BacktestResult {
		code: string;
		total_bars: number;
		total_trades: number;
		cum_return_pct: number;
		annualized_pct: number;
		mdd_pct: number;
		win_rate_pct: number;
		risk_reward: number;
		buy_hold_return_pct?: number | null;
		excess_return_pct?: number | null;
		avg_trade_cost_bps?: number;
		validation_warnings?: string[];
		validation_log_count?: number | null;
		validation?: {
			parameter_stability: Array<{
				buy_threshold: number;
				take_profit_pct: number;
				fallback_stop_pct: number;
				total_trades: number;
				cum_return_pct: number;
				excess_return_pct: number | null;
			}>;
			walk_forward: Array<{
				window: number;
				start_time: string;
				end_time: string;
				total_trades: number;
				cum_return_pct: number;
				excess_return_pct: number | null;
			}>;
		} | null;
		cost_assumptions?: {
			fee_bps: number;
			sell_tax_bps: number;
			slippage_bps: number;
			spread_bps: number;
		};
		trades: BacktestTrade[];
	}

	// 파라미터 기본값
	let days = $state(365);
	let tpPct = $state(5.0);
	let maxBars = $state(20);
	let loading = $state(false);
	let result = $state<BacktestResult | null>(null);
	let error = $state<string | null>(null);

	// POST /api/backtest 호출
	async function job() {
		const code = $selectedStock;
		if (!code) return;

		loading = true;
		error = null;
		result = null;

		try {
			const resp = await fetch('/api/backtest', {
				method: 'POST',
				headers: { 'Content-Type': 'application/json' },
				body: JSON.stringify({
					code,
					days,
					take_profit_pct: tpPct,
					max_hold_bars: maxBars,
				}),
			});

			if (!resp.ok) {
				const data = await resp.json().catch(() => ({ detail: resp.statusText }));
				error = data.detail || `Error ${resp.status}`;
				return;
			}

			result = await resp.json();
		} catch (e) {
			error = '서버 연결 실패';
		} finally {
			loading = false;
		}
	}

	// 소수 자리 포맷
	function num(n: number, digits = 2): string {
		return n.toFixed(digits);
	}

	// 원화 반올림 콤마 포맷
	function won(n: number): string {
		return Math.round(n).toLocaleString('ko-KR');
	}

	// 퍼센트 포맷 (+부호, null 안전)
	function pct(n: number | null | undefined): string {
		return typeof n === 'number' ? `${n > 0 ? '+' : ''}${num(n)}%` : '-';
	}

	// 청산 사유 한글 변환
	function why(r: string): string {
		if (r === 'stop') return '손절';
		if (r === 'tp') return '익절';
		return '보유만기';
	}

	// 청산 사유 → 색상 클래스
	function tone(r: string): string {
		if (r === 'stop') return 'reason-stop';
		if (r === 'tp') return 'reason-tp';
		return 'reason-trail';
	}
</script>

<div class="backtest-panel">
	<!-- Parameter Form -->
	<div class="backtest-form">
		<label class="form-group">
			<span>기간 (일)</span>
			<input type="number" bind:value={days} min="7" max="730" />
		</label>
		<label class="form-group">
			<span>익절 (%)</span>
			<input type="number" bind:value={tpPct} min="1" max="20" step="0.5" />
		</label>
		<label class="form-group">
			<span>최대보유 (봉)</span>
			<input type="number" bind:value={maxBars} min="5" max="100" />
		</label>
		<button class="btn-run" onclick={job} disabled={loading || !$selectedStock}>
			{loading ? '실행 중' : '실행'}
		</button>
	</div>

	{#if loading}
		<div class="backtest-loading">
			<div class="backtest-spinner"></div>
			<span>백테스트 실행 중 (팩터 계산 포함)</span>
		</div>
	{:else if error}
		<div class="backtest-error">{error}</div>
	{:else if result}
		<!-- Metrics Grid -->
		<div class="metrics-grid">
			<div class="metric-tile">
				<span class="label">누적수익률</span>
				<span class="value" class:up={result.cum_return_pct > 0} class:down={result.cum_return_pct < 0}>
					{result.cum_return_pct > 0 ? '+' : ''}{num(result.cum_return_pct)}%
				</span>
			</div>
			<div class="metric-tile">
				<span class="label">연환산</span>
				<span class="value" class:up={result.annualized_pct > 0} class:down={result.annualized_pct < 0}>
					{result.annualized_pct > 0 ? '+' : ''}{num(result.annualized_pct)}%
				</span>
			</div>
			<div class="metric-tile">
				<span class="label">MDD</span>
				<span class="value down">{num(result.mdd_pct)}%</span>
			</div>
			<div class="metric-tile">
				<span class="label">승률</span>
				<span class="value">{num(result.win_rate_pct, 1)}%</span>
			</div>
			<div class="metric-tile">
				<span class="label">손익비</span>
				<span class="value">{num(result.risk_reward)}</span>
			</div>
			<div class="metric-tile">
				<span class="label">거래횟수</span>
				<span class="value">{result.total_trades}</span>
			</div>
			<div class="metric-tile">
				<span class="label">B&H 대비</span>
				<span class="value" class:up={(result.excess_return_pct ?? 0) > 0} class:down={(result.excess_return_pct ?? 0) < 0}>
					{pct(result.excess_return_pct)}
				</span>
			</div>
			<div class="metric-tile">
				<span class="label">왕복비용</span>
				<span class="value">{num(result.avg_trade_cost_bps ?? 0, 1)}bp</span>
			</div>
		</div>

		{#if result.validation_warnings?.length}
			<div class="backtest-error">
				{#each result.validation_warnings as warning}
					<div>{warning}</div>
				{/each}
			</div>
		{/if}

		{#if result.cost_assumptions}
			<div class="backtest-note">
				비용 가정: 수수료 {num(result.cost_assumptions.fee_bps, 1)}bp,
				매도세 {num(result.cost_assumptions.sell_tax_bps, 1)}bp,
				슬리피지 {num(result.cost_assumptions.slippage_bps, 1)}bp,
				스프레드 {num(result.cost_assumptions.spread_bps, 1)}bp
				{#if result.validation_log_count}
					· WF 기록 {result.validation_log_count}회
				{/if}
			</div>
		{/if}

		{#if result.validation}
			<div class="trades-section">
				<div class="trades-header">검증 요약</div>
				<table class="trades-table">
					<thead>
						<tr>
							<th>구분</th>
							<th>표본</th>
							<th>거래</th>
							<th>수익률</th>
							<th>B&H 대비</th>
						</tr>
					</thead>
					<tbody>
						{#each result.validation.walk_forward as row}
							<tr>
								<td>WF {row.window}</td>
								<td>{row.start_time.slice(5)} ~ {row.end_time.slice(5)}</td>
								<td>{row.total_trades}</td>
								<td class:up={row.cum_return_pct > 0} class:down={row.cum_return_pct < 0}>{pct(row.cum_return_pct)}</td>
								<td class:up={(row.excess_return_pct ?? 0) > 0} class:down={(row.excess_return_pct ?? 0) < 0}>{pct(row.excess_return_pct)}</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}

		<!-- Trade List -->
		{#if result.trades.length > 0}
			<div class="trades-section">
				<div class="trades-header">거래 내역 ({result.trades.length}건)</div>
				<table class="trades-table">
					<thead>
						<tr>
							<th>진입시간</th>
							<th>진입가</th>
							<th>청산시간</th>
							<th>청산가</th>
							<th>사유</th>
							<th>수익률</th>
						</tr>
					</thead>
					<tbody>
						{#each result.trades as trade}
							<tr>
								<td>{trade.entry_time}</td>
								<td>{won(trade.entry_price)}</td>
								<td>{trade.exit_time}</td>
								<td>{won(trade.exit_price)}</td>
								<td><span class={tone(trade.exit_reason)}>{why(trade.exit_reason)}</span></td>
								<td class:up={trade.pnl_pct > 0} class:down={trade.pnl_pct < 0}>
									{trade.pnl_pct > 0 ? '+' : ''}{num(trade.pnl_pct)}%
								</td>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{:else}
			<div class="backtest-empty">해당 기간 매매 시그널 없음</div>
		{/if}
	{:else}
		<div class="backtest-empty">
			종목을 선택하고 파라미터를 설정한 후 실행하세요.<br />
			CandleStore에 축적된 15분봉 데이터를 기반으로 백테스트합니다.
		</div>
	{/if}
</div>
