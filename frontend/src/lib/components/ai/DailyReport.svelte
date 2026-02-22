<script lang="ts">
	import { dailyReport, fetchDailyReport } from '$lib/stores/ai';
	import './DailyReport.css';

	let loading = $state(false);

	$effect(() => {
		if (!$dailyReport) {
			loading = true;
			fetchDailyReport().finally(() => loading = false);
		}
	});
</script>

<div class="report-panel">
	{#if loading}
		<div class="report-loading">리포트 생성 중...</div>
	{:else if $dailyReport}
		<div class="report-content">{$dailyReport}</div>
	{:else}
		<div class="report-empty">리포트를 생성할 수 없습니다. GEMINI_API_KEY를 확인하세요.</div>
	{/if}
</div>
