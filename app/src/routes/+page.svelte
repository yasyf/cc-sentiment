<script lang="ts">
	import Dashboard from '$lib/Dashboard.svelte';
	import { verdictFor } from '$lib/verdict.js';
	import { describeTrend } from '$lib/trend.js';
	import type { PageProps } from './$types.js';

	let { data }: PageProps = $props();

	const overallAvg = $derived.by(() => {
		const total = data.distribution.reduce((s, d) => s + d.count, 0);
		if (total === 0) return 0;
		return data.distribution.reduce((s, d) => s + d.score * d.count, 0) / total;
	});

	const verdictText = $derived(verdictFor(overallAvg).text);
	const trendDescription = $derived(describeTrend(data.trend.sentiment_current, data.trend.sentiment_previous));

	const description = $derived(
		`${verdictText} ${overallAvg.toFixed(1)}/5 across ${data.total_sessions.toLocaleString()} sessions. ${trendDescription}.`
	);
</script>

<svelte:head>
	<title>cc-sentiment</title>
	<meta name="description" content={`${verdictText} Average sentiment is ${overallAvg.toFixed(1)}/5 across ${data.total_sessions.toLocaleString()} sessions. Live data scored on-device.`} />
	<meta property="og:title" content="cc-sentiment" />
	<meta property="og:description" content={description} />
	<meta property="og:image" content="https://sentiments.cc/og" />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:title" content="cc-sentiment" />
	<meta name="twitter:description" content={description} />
	<meta name="twitter:image" content="https://sentiments.cc/og" />
</svelte:head>

<Dashboard {data} />
