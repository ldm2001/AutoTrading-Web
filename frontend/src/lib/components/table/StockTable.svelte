<script lang="ts">
	import {
		allStocks,
		selectedStock,
		searchSuggestions,
		type SearchSuggestion
	} from '$lib/stores/stocks';
	import { recommendations, recommendLoading, fetchRecommendations } from '$lib/stores/recommend';
	import type { RecommendStock } from '$lib/types';
	import Modal from '$lib/components/modal/Modal.svelte';
	import './StockTable.css';

	let searchInput = $state('');
	let searchError = $state('');
	let suggestions = $state<SearchSuggestion[]>([]);
	let showSuggestions = $state(false);
	let debounceTimer: ReturnType<typeof setTimeout>;
	let recModal = $state<RecommendStock | null>(null);

	const filteredStocks = $derived.by(() => {
		const q = searchInput.trim().toLowerCase();
		if (!q) return $allStocks;
		return $allStocks.filter(
			(s) => s.name.toLowerCase().includes(q) || s.code.includes(q)
		);
	});

	function handleInput() {
		searchError = '';
		clearTimeout(debounceTimer);
		const q = searchInput.trim();
		if (q.length < 1) {
			suggestions = [];
			showSuggestions = false;
			return;
		}
		debounceTimer = setTimeout(async () => {
			suggestions = await searchSuggestions(q);
			showSuggestions = suggestions.length > 0;
		}, 300);
	}

	function pickSuggestion(item: SearchSuggestion) {
		showSuggestions = false;
		searchInput = '';
		selectedStock.set(item.code);
	}

	function handleSearch(e: SubmitEvent) {
		e.preventDefault();
		const q = searchInput.trim().toLowerCase();
		if (!q) return;
		showSuggestions = false;
		const filtered = $allStocks.filter(
			(s) => s.name.toLowerCase().includes(q) || s.code.includes(q)
		);
		if (filtered.length > 0) {
			selectedStock.set(filtered[0].code);
			searchInput = '';
		} else {
			searchError = '종목을 찾을 수 없습니다';
		}
	}

	function handleBlur() {
		setTimeout(() => (showSuggestions = false), 200);
	}

	function select(code: string) {
		selectedStock.set(code);
	}

	function openRecModal(rec: RecommendStock) {
		selectedStock.set(rec.code);
		recModal = rec;
	}

	function signalLabel(signal: string): string {
		if (signal === 'buy') return '매수';
		if (signal === 'sell') return '매도';
		return '관망';
	}

	function signalNarrative(rec: RecommendStock): string {
		const s = rec.signal;
		const score = rec.score.toFixed(0);
		const factors = rec.factors ?? [];

		// 긍정/부정 팩터 정렬
		const positives = factors.filter(f => f.score > 0).sort((a, b) => b.score - a.score);
		const negatives = factors.filter(f => f.score < 0).sort((a, b) => a.score - b.score);
		const topPos = positives.slice(0, 2).map(f => f.reason);
		const topNeg = negatives.slice(0, 1).map(f => f.reason);

		let detail = '';
		if (topPos.length > 0) detail += topPos.join(', ');
		if (topNeg.length > 0) detail += (detail ? '. 다만, ' : '') + topNeg.join(', ');

		if (s === 'buy')
			return `종합 스코어 ${score}점으로 매수 구간입니다. ${detail}.`;
		if (s === 'sell')
			return `종합 스코어 ${score}점으로 매도 구간입니다. ${detail}.`;
		return `종합 스코어 ${score}점으로 관망 구간입니다.${detail ? ' ' + detail + '.' : ''}`;
	}

	function fmtPrice(n: number): string {
		return n.toLocaleString('ko-KR');
	}
</script>

<div class="watchlist-wrap">
	<!-- 상단: 전체 종목 -->
	<div class="watchlist-section my-stocks">
		<div class="watchlist-header">
			<span class="watchlist-title">종목 <span class="watchlist-count">{$allStocks.length}</span></span>
		</div>

		<div class="watchlist-search">
			<form onsubmit={handleSearch} class="search-form">
				<input
					bind:value={searchInput}
					oninput={handleInput}
					onfocus={handleInput}
					onblur={handleBlur}
					type="text"
					placeholder="종목명/코드 검색..."
					class="search-input"
				/>
				{#if searchInput}
					<button type="button" class="search-clear" onclick={() => { searchInput = ''; suggestions = []; showSuggestions = false; }}>
						&times;
					</button>
				{/if}
			</form>

			{#if showSuggestions}
				<div class="suggestions">
					{#each suggestions as item (item.code)}
						<button
							type="button"
							class="suggestion-item"
							onclick={() => pickSuggestion(item)}
						>
							<span class="suggestion-name">{item.name}</span>
							<span class="suggestion-code">{item.code}</span>
						</button>
					{/each}
				</div>
			{/if}

			{#if searchError}
				<span class="search-error">{searchError}</span>
			{/if}
		</div>

		<div class="stock-list">
			{#each filteredStocks as stock (stock.code)}
				<button
					class="stock-item"
					class:selected={$selectedStock === stock.code}
					onclick={() => select(stock.code)}
				>
					<span class="stock-name">{stock.name}</span>
					<span class="stock-market-tag">{stock.market}</span>
				</button>
			{/each}

			{#if filteredStocks.length === 0}
				<div class="watchlist-empty">
					{searchInput ? '검색 결과가 없습니다' : '종목을 불러오는 중...'}
				</div>
			{/if}
		</div>
	</div>

	<!-- 구분선 -->
	<div class="section-divider"></div>

	<!-- 하단: AI 추천 -->
	<div class="watchlist-section ai-picks">
		<div class="watchlist-header ai-header">
			<span class="watchlist-title ai-title">AI 추천</span>
			<button class="refresh-btn" onclick={fetchRecommendations} disabled={$recommendLoading}>
				{$recommendLoading ? '분석중...' : '새로고침'}
			</button>
		</div>

		<div class="stock-list">
			{#if $recommendLoading}
				<div class="recommend-loading">
					<div class="recommend-spinner"></div>
					<span>전략 분석 중...</span>
				</div>
			{:else if $recommendations.length === 0}
				<div class="watchlist-empty">추천 종목을 불러오려면<br/>새로고침을 눌러주세요</div>
			{:else}
				{#each $recommendations as rec (rec.code)}
					<button
						class="stock-item recommend-item"
						class:selected={$selectedStock === rec.code}
						onclick={() => openRecModal(rec)}
					>
						<span class="stock-name">{rec.name}</span>
						<span class="rec-signal {rec.signal}">{signalLabel(rec.signal)}</span>
					</button>
				{/each}
			{/if}
		</div>
	</div>
</div>

<!-- AI 추천 상세 팝업 -->
{#if recModal}
	{@const rec = recModal}
	<Modal
		open={true}
		title="AI 추천 분석 — {rec.name}"
		onclose={() => recModal = null}
	>
		<div class="rec-modal-body">
			<!-- 시그널 + 점수 + 현재가 -->
			<div class="rec-modal-signal-row">
				<span class="rec-modal-signal {rec.signal}">{signalLabel(rec.signal)}</span>
				<span class="rec-modal-score">{rec.score > 0 ? '+' : ''}{rec.score.toFixed(0)}점 / 100</span>
				<span class="rec-modal-price">{fmtPrice(rec.price)}원</span>
			</div>

			<!-- 종합 설명 문장 -->
			<p class="rec-modal-narrative">{signalNarrative(rec)}</p>

			<!-- 팩터별 근거 -->
			{#if rec.factors && rec.factors.length > 0}
				<div class="rec-modal-section">
					<h4 class="rec-modal-section-title">팩터별 분석</h4>
					{#each rec.factors as f}
						<div class="rec-modal-factor">
							<div class="rec-modal-factor-top">
								<span class="rec-modal-factor-name">{f.name}</span>
								<div class="rec-modal-bar-wrap">
									<span
										class="rec-modal-bar"
										class:pos={f.score > 0}
										class:neg={f.score < 0}
										class:neutral={f.score === 0}
										style="transform: scaleX({f.score === 0 ? 1 : Math.min(Math.abs(f.score) / f.max, 1)})"
									></span>
								</div>
								<span class="rec-modal-factor-score" class:pos={f.score > 0} class:neg={f.score < 0}>
									{f.score > 0 ? '+' : ''}{f.score.toFixed(0)}
								</span>
							</div>
							<p class="rec-modal-factor-reason">{f.reason}</p>
						</div>
					{/each}
				</div>
			{/if}

			<!-- Transformer 예측 -->
			{#if rec.prediction}
				{@const p = rec.prediction}
				<div class="rec-modal-section">
					<h4 class="rec-modal-section-title">AI Transformer 5일 예측</h4>
					<div class="rec-modal-pred-row">
						<div class="rec-modal-pred-item">
							<span class="rec-modal-pred-label">현재가</span>
							<span class="rec-modal-pred-val">{fmtPrice(p.current_price)}원</span>
						</div>
						<span class="rec-modal-pred-arrow">→</span>
						<div class="rec-modal-pred-item">
							<span class="rec-modal-pred-label">5일 후 예측</span>
							<span class="rec-modal-pred-val" class:pred-up={p.change_pct > 0} class:pred-down={p.change_pct < 0}>
								{fmtPrice(p.predicted_5d)}원
							</span>
						</div>
						<span class="rec-modal-pred-badge" class:pred-up={p.change_pct > 0} class:pred-down={p.change_pct < 0}>
							{p.change_pct > 0 ? '+' : ''}{p.change_pct.toFixed(1)}%&nbsp;{p.trend === '상승' ? '↑' : p.trend === '하락' ? '↓' : '→'}
						</span>
					</div>
				</div>
			{/if}
		</div>
	</Modal>
{/if}
