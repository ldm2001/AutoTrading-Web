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
	import Modal from '$lib/components/modal/Modal.svelte';
	import { get } from 'svelte/store';
	import { indices, initAllStocks, fetchIndices, selectedStock, selectedStockDetail, fetchStockPrice, updateStockPrices } from '$lib/stores/stocks';
	import { fetchTradingStatus, addConsoleMessage } from '$lib/stores/trading';
	import { priceWs, tradeWs } from '$lib/stores/websocket';
	import { fetchRecommendations } from '$lib/stores/recommend';
	import type { PriceUpdate, TradeMessage } from '$lib/types';
	import './page.css';

	let showAI     = $state(false);
	let showNews   = $state(false);
	let showReport = $state(false);
	let showPredict = $state(false);

	const stockInfo = $derived($selectedStockDetail);

	// 종목 선택 시 가격 조회
	$effect(() => {
		const code = $selectedStock;
		if (code) {
			fetchStockPrice(code);
		}
	});

	onMount(() => {
		Promise.all([initAllStocks(), fetchIndices(), fetchTradingStatus(), fetchRecommendations()]);

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
	<Header />

	<main class="app-main">
		<IndexPanel />

		<!-- Stock Info Bar -->
		{#if stockInfo}
			<div class="info-bar">
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

				<div class="action-bar">
					<button class="action-btn ai" onclick={() => showAI = true}>
						AI 분석
					</button>
					<button class="action-btn predict" onclick={() => showPredict = true}>
						AI 예측
					</button>
					<button class="action-btn news" onclick={() => showNews = true}>
						뉴스
					</button>
					<button class="action-btn report" onclick={() => showReport = true}>
						리포트
					</button>
				</div>

				<TradeConsole />
			</div>

			<!-- Right: Order Panel (always visible) -->
			<div class="col-right">
				<OrderPanel />
			</div>
		</div>
	</main>
</div>

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
