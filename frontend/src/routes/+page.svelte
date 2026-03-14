<script lang="ts">
	import { onMount } from 'svelte';
	import Header from '$lib/components/header/Header.svelte';
	import StockTable from '$lib/components/table/StockTable.svelte';
	import StockChart from '$lib/components/chart/StockChart.svelte';
	import IndexPanel from '$lib/components/index/IndexPanel.svelte';
	import TradeConsole from '$lib/components/trade/TradeConsole.svelte';
	import OrderPanel from '$lib/components/trade/OrderPanel.svelte';
	import AISignalPanel from '$lib/components/ai/AISignalPanel.svelte';
	import NewsPanel from '$lib/components/ai/NewsPanel.svelte';
	import DailyReport from '$lib/components/ai/DailyReport.svelte';
	import PredictPanel from '$lib/components/predict/PredictPanel.svelte';
	import BacktestPanel from '$lib/components/backtest/BacktestPanel.svelte';
	import SectorHeatmap from '$lib/components/heatmap/SectorHeatmap.svelte';
	import HoldingsPanel from '$lib/components/portfolio/HoldingsPanel.svelte';
	import TradeHistoryPanel from '$lib/components/portfolio/TradeHistoryPanel.svelte';
	import ProfitPanel from '$lib/components/portfolio/ProfitPanel.svelte';
	import VolatilityPanel from '$lib/components/market/VolatilityPanel.svelte';
	import SectorFlowPanel from '$lib/components/market/SectorFlowPanel.svelte';
	import Modal from '$lib/components/modal/Modal.svelte';
	import { fly } from 'svelte/transition';
	import { get } from 'svelte/store';
	import { indices, initAllStocks, fetchIndices, selectedStock, selectedStockDetail, fetchStockPrice, updateStockPrices } from '$lib/stores/stocks';
	import { tradingStatus, fetchTradingStatus, fetchWatchlist, flipWatchlist, watchCodes, watchBusy, addConsoleMessage } from '$lib/stores/trading';
	import { priceWs, tradeWs } from '$lib/stores/websocket';
	import { fetchRecommendations } from '$lib/stores/recommend';
	import type { PriceUpdate, TradeMessage } from '$lib/types';
	import './page.css';

	let showAI     = $state(false);
	let showNews   = $state(false);
	let showReport = $state(false);
	let showPredict = $state(false);
	let showOrder    = $state(false);
	let showAuto     = $state(false);
	let showBacktest  = $state(false);
	let showHeatmap   = $state(false);
	let showHoldings  = $state(false);
	let showHistory   = $state(false);
	let showProfit    = $state(false);
	let showVolatility = $state(false);
	let showSectorFlow = $state(false);
	let menuOpen       = $state(false);

	const menuMap: Record<string, () => void> = {
		report:     () => showReport = true,
		heatmap:    () => showHeatmap = true,
		ai:         () => showAI = true,
		predict:    () => showPredict = true,
		news:       () => showNews = true,
		order:      () => showOrder = true,
		auto:       () => showAuto = true,
		backtest:   () => showBacktest = true,
		holdings:   () => showHoldings = true,
		history:    () => showHistory = true,
		profit:     () => showProfit = true,
		volatility: () => showVolatility = true,
		sectorflow: () => showSectorFlow = true,
	};

	function openMenu(key: string) {
		menuOpen = false;
		menuMap[key]?.();
	}

	const stockInfo = $derived($selectedStockDetail);
	const autoOn = $derived.by(() => !!$selectedStock && $watchCodes.includes($selectedStock));
	const autoLive = $derived.by(() => autoOn && $tradingStatus.is_running);
	const autoLabel = $derived.by(() => {
		if (autoLive) return '자동매매 중';
		if (autoOn) return '자동대기';
		return '자동매매';
	});

	function pct(value: number | undefined): string {
		if (value == null) return '-';
		return `${(value * 100).toFixed(0)}%`;
	}

	async function applyAuto() {
		const code = $selectedStock;
		const name = stockInfo?.name ?? code;
		if (!code) return;
		const result = await flipWatchlist(code);
		if (!result.ok) return;
		addConsoleMessage(
			result.active
				? `[AUTO] ${name} 자동매매 대상에 등록했습니다.`
				: `[AUTO] ${name} 자동매매 대상에서 제외했습니다.`
		);
		showAuto = false;
	}

	// 종목 선택 시 가격 조회
	$effect(() => {
		const code = $selectedStock;
		if (code) {
			fetchStockPrice(code);
		}
	});

	onMount(() => {
		Promise.all([initAllStocks(), fetchIndices(), fetchTradingStatus(), fetchWatchlist(), fetchRecommendations()]);

		priceWs.connect();
		tradeWs.connect();

		const offPrice = priceWs.on('price_update', (msg: unknown) => {
			const data = msg as PriceUpdate;
			if (data.stocks) updateStockPrices(data.stocks);
			// 부분 실패 시 기존 값 유지 — merge로 깜빡임 방지
			if (data.indices?.length) {
				indices.update(cur => {
					const map = new Map(cur.map(i => [i.code, i]));
					for (const idx of data.indices) map.set(idx.code, idx);
					return [...map.values()];
				});
			}
		});

		const offMsg = tradeWs.on('message', (msg: unknown) => {
			const data = msg as TradeMessage;
			if (typeof data.data === 'string') {
				addConsoleMessage(data.data);
			}
		});

		const offTrade = tradeWs.on('trade', (_msg: unknown) => {
			fetchTradingStatus();
		});

		// 선택 종목 가격 주기 갱신 — symbol_list 미포함 종목도 5초마다 최신가 유지
		const priceTimer = setInterval(() => {
			const code = get(selectedStock);
			if (code) fetchStockPrice(code);
		}, 5000);

		return () => {
			offPrice(); offMsg(); offTrade();
			priceWs.close();
			tradeWs.close();
			clearInterval(priceTimer);
		};
	});
</script>

<div class="app-shell">
	<Header onmenu={() => menuOpen = !menuOpen} />

	{#if menuOpen}
		<!-- svelte-ignore a11y_click_events_have_key_events -->
		<!-- svelte-ignore a11y_no_static_element_interactions -->
		<div class="menu-backdrop" onclick={() => menuOpen = false}></div>
		<nav class="menu-dropdown">
			<span class="menu-label">마켓</span>
			<button class="menu-item" onclick={() => openMenu('report')}>
				<span class="menu-dot report"></span>리포트
			</button>
			<button class="menu-item" onclick={() => openMenu('heatmap')}>
				<span class="menu-dot heatmap"></span>섹터맵
			</button>

			{#if stockInfo}
				<span class="menu-label">분석</span>
				<button class="menu-item" onclick={() => openMenu('ai')}>
					<span class="menu-dot ai"></span>AI 분석
				</button>
				<button class="menu-item" onclick={() => openMenu('predict')}>
					<span class="menu-dot predict"></span>AI 예측
				</button>
				<button class="menu-item" onclick={() => openMenu('news')}>
					<span class="menu-dot news"></span>뉴스
				</button>

				<span class="menu-label">매매</span>
				<button class="menu-item" onclick={() => openMenu('order')}>
					<span class="menu-dot order"></span>주식주문
				</button>
				<button class="menu-item" onclick={() => openMenu('auto')}>
					<span class="menu-dot auto" class:ready={autoOn} class:live={autoLive}></span>
					{autoLabel}
				</button>
				<button class="menu-item" onclick={() => openMenu('backtest')}>
					<span class="menu-dot backtest"></span>백테스트
				</button>
			{/if}

			<span class="menu-label">포트폴리오</span>
			<button class="menu-item" onclick={() => openMenu('holdings')}>
				<span class="menu-dot holdings"></span>보유종목
			</button>
			<button class="menu-item" onclick={() => openMenu('history')}>
				<span class="menu-dot history"></span>체결내역
			</button>
			<button class="menu-item" onclick={() => openMenu('profit')}>
				<span class="menu-dot profit"></span>수익현황
			</button>

			<span class="menu-label">시장분석</span>
			<button class="menu-item" onclick={() => openMenu('sectorflow')}>
				<span class="menu-dot sectorflow"></span>업종흐름
			</button>
		</nav>
	{/if}

	<main class="app-main">
		<IndexPanel />

		<!-- Stock Info Bar -->
		{#if stockInfo}
			<div class="info-bar">
				<div class="info-main">
					<div class="info-left">
						<span class="info-name">{stockInfo.name}</span>
						<span class="info-code">{stockInfo.code}</span>
						<span class="info-market">{stockInfo.market}</span>
					</div>
					<div class="info-right">
						<span class="info-price" class:up={stockInfo.change > 0} class:down={stockInfo.change < 0}>
							{stockInfo.price.toLocaleString('ko-KR')}
						</span>
						<span class="info-change" class:up={stockInfo.change > 0} class:down={stockInfo.change < 0}>
							{stockInfo.change > 0 ? '▲' : stockInfo.change < 0 ? '▼' : ''}{Math.abs(stockInfo.change).toLocaleString()}
							({stockInfo.change_percent > 0 ? '+' : ''}{stockInfo.change_percent.toFixed(2)}%)
						</span>
						<span class="info-sep">|</span>
						<span class="info-detail">거래량 <strong>{stockInfo.volume.toLocaleString()}</strong></span>
						<span class="info-detail">시총 <strong>{stockInfo.market_cap}</strong></span>
					</div>
				</div>

				</div>
		{/if}

		<!-- 3-Column Layout -->
		<div class="main-grid">
			<!-- Left: Watchlist -->
			<div class="col-left">
				<StockTable />
			</div>

			<!-- Center: Chart + Actions + Console -->
			<div class="col-center">
				<StockChart />

				<TradeConsole />
			</div>

			</div>
	</main>
</div>

{#if showOrder}
	<!-- svelte-ignore a11y_click_events_have_key_events -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="order-backdrop" onclick={() => showOrder = false}></div>
	<div class="order-drawer" transition:fly={{ x: 360, duration: 200, opacity: 1 }}>
		<OrderPanel onclose={() => showOrder = false} />
	</div>
{/if}

<Modal open={showAI} title="AI 분석 - {stockInfo?.name ?? $selectedStock}" onclose={() => showAI = false}>
	<AISignalPanel />
</Modal>

<Modal open={showNews} title="뉴스 감성 - {stockInfo?.name ?? $selectedStock}" onclose={() => showNews = false}>
	<NewsPanel />
</Modal>

<Modal open={showReport} title="일일 마켓 리포트" onclose={() => showReport = false}>
	<DailyReport />
</Modal>

<Modal open={showPredict} title="AI 주가 예측 - {stockInfo?.name ?? $selectedStock}" onclose={() => showPredict = false}>
	<PredictPanel />
</Modal>

<Modal open={showAuto} title="자동매매 설정 - {stockInfo?.name ?? $selectedStock}" onclose={() => showAuto = false}>
	<div class="auto-modal">
		<div class="auto-head">
			<div>
				<p class="auto-name">{stockInfo?.name ?? $selectedStock}</p>
				<p class="auto-copy">
					{autoLive
						? '현재 이 종목은 자동매매 감시 대상이며 전역 엔진이 실행 중입니다.'
						: autoOn
							? '현재 이 종목은 자동매매 감시 대상입니다. 전역 엔진이 켜지면 스캔에 포함됩니다.'
							: '이 종목을 자동매매 감시 대상에 추가할 수 있습니다.'}
				</p>
			</div>
			<span class="auto-state" class:active={autoOn} class:live={autoLive}>
				{autoLive ? 'LIVE' : autoOn ? 'READY' : 'MANUAL'}
			</span>
		</div>

		<div class="auto-grid">
			<div class="auto-cell">
				<span class="auto-label">현재가</span>
				<strong>{stockInfo ? `${stockInfo.price.toLocaleString('ko-KR')}원` : '-'}</strong>
			</div>
			<div class="auto-cell">
				<span class="auto-label">전역 상태</span>
				<strong>{$tradingStatus.is_running ? '실행중' : '대기'}</strong>
			</div>
			<div class="auto-cell">
				<span class="auto-label">종목당 매수</span>
				<strong>{pct($tradingStatus.plan?.buy_percent)}</strong>
			</div>
			<div class="auto-cell">
				<span class="auto-label">매수 기준</span>
				<strong>{$tradingStatus.plan?.buy_score_threshold ?? '-'}점</strong>
			</div>
			<div class="auto-cell">
				<span class="auto-label">손절</span>
				<strong>{$tradingStatus.plan?.stop_loss_pct ?? '-'}%</strong>
			</div>
			<div class="auto-cell">
				<span class="auto-label">익절</span>
				<strong>{$tradingStatus.plan ? `+${$tradingStatus.plan.take_profit_pct}%` : '-'}</strong>
			</div>
		</div>

		<div class="auto-plan">
			<div class="auto-card">
				<span class="auto-kicker">전략</span>
				<strong>멀티팩터 자동매매</strong>
				<p>현재 종목은 워치리스트 기반으로 5분 주기 스캔에 포함됩니다.</p>
			</div>
			<div class="auto-card">
				<span class="auto-kicker">실행 조건</span>
				<div class="auto-tags">
					<span class="auto-tag">최대 {$tradingStatus.plan?.target_buy_count ?? '-'}종목</span>
					<span class="auto-tag">스캔 {$tradingStatus.watch_count ?? 0}개</span>
					<span class="auto-tag">매수 {$tradingStatus.plan?.buy_score_threshold ?? '-'}점+</span>
				</div>
			</div>
		</div>

		<p class="auto-note" class:off={!$tradingStatus.is_running}>
			{$tradingStatus.is_running
				? '시작 전 워치리스트 편입 여부와 손절 라인을 확인하세요. 전역 스위치를 끄면 모든 자동매매가 즉시 중지됩니다.'
				: '현재 전체 자동매매가 OFF 상태입니다. 지금 등록하면 대기 상태로만 보관되고, 전역 스위치를 켠 뒤부터 스캔에 포함됩니다.'}
		</p>

		<div class="auto-actions">
			<button class="auto-btn ghost" type="button" onclick={() => showAuto = false}>
				닫기
			</button>
			<button class="auto-btn strong" type="button" onclick={applyAuto} disabled={$watchBusy}>
				{$watchBusy ? '처리중' : autoOn ? '자동매매 제외' : '자동매매 등록'}
			</button>
		</div>
	</div>
</Modal>

<Modal open={showBacktest} title="백테스트 - {stockInfo?.name ?? $selectedStock}" onclose={() => showBacktest = false}>
	<BacktestPanel />
</Modal>

<Modal open={showHeatmap} title="섹터 히트맵" onclose={() => showHeatmap = false}>
	<SectorHeatmap />
</Modal>

<Modal open={showHoldings} title="보유종목 현황" onclose={() => showHoldings = false}>
	<HoldingsPanel />
</Modal>

<Modal open={showHistory} title="체결내역" onclose={() => showHistory = false}>
	<TradeHistoryPanel />
</Modal>

<Modal open={showProfit} title="수익현황" onclose={() => showProfit = false}>
	<ProfitPanel />
</Modal>

{#if stockInfo}
	<Modal open={showVolatility} title="변동성 분석 - {stockInfo.name}" onclose={() => showVolatility = false}>
		<VolatilityPanel code={stockInfo.code} name={stockInfo.name} />
	</Modal>
{/if}

<Modal open={showSectorFlow} title="업종별 흐름" onclose={() => showSectorFlow = false}>
	<SectorFlowPanel />
</Modal>
