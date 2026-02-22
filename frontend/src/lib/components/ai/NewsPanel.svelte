<script lang="ts">
	import { newsSentiment } from '$lib/stores/ai';
	import './NewsPanel.css';

	function sentimentColor(score: number): string {
		if (score > 20) return 'positive';
		if (score < -20) return 'negative';
		return 'neutral';
	}

	function sentimentLabel(sentiment: string): string {
		switch (sentiment) {
			case 'positive': return '긍정';
			case 'negative': return '부정';
			default: return '중립';
		}
	}
</script>

<div class="news-panel">
	<div class="news-panel-header">
		<span class="news-panel-title">뉴스 감성</span>
		{#if $newsSentiment}
			<span class="news-score" class:positive={$newsSentiment.score > 20} class:negative={$newsSentiment.score < -20} class:neutral-score={$newsSentiment.score >= -20 && $newsSentiment.score <= 20}>
				{$newsSentiment.score > 0 ? '+' : ''}{$newsSentiment.score}
			</span>
		{/if}
	</div>

	{#if $newsSentiment}
		{#if $newsSentiment.summary}
			<p class="news-summary">{$newsSentiment.summary}</p>
		{/if}

		<div class="news-list">
			{#each $newsSentiment.articles as article}
				<div class="news-item">
					<span class="news-sentiment-dot" class:positive={article.sentiment === 'positive'} class:negative={article.sentiment === 'negative'} class:neutral={article.sentiment === 'neutral'}></span>
					<span class="news-title">{article.title}</span>
					<span class="news-article-sentiment {sentimentColor(article.score)}">
						{sentimentLabel(article.sentiment)}
					</span>
				</div>
			{/each}
		</div>

		{#if $newsSentiment.articles.length === 0}
			<div class="news-empty">관련 뉴스가 없습니다</div>
		{/if}
	{:else}
		<div class="news-empty">종목을 선택하면 뉴스 분석이 시작됩니다</div>
	{/if}
</div>
