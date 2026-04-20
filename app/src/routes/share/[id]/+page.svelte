<script lang="ts">
	import Dashboard from '$lib/Dashboard.svelte';
	import type { PageProps } from './$types.js';

	let { data }: PageProps = $props();

	const ogImage = $derived(data.ogImageUrl);
	const shareUrl = $derived(`https://sentiments.cc/share/${data.record.id}`);

	const handleLabel = $derived(
		data.record.contributor_type === 'github' || data.record.contributor_type === 'gist'
			? `@${data.record.contributor_id}`
			: null,
	);

	const description = $derived(
		data.stat
			? (handleLabel ? `${handleLabel} is ${data.stat.text}.` : `${data.stat.text}.`)
			: 'Live sentiment from real Claude Code sessions, scored on-device.'
	);

	const title = $derived(
		handleLabel ? `cc-sentiment · ${handleLabel}` : 'cc-sentiment'
	);
</script>

<svelte:head>
	<title>{title}</title>
	<meta name="description" content={description} />
	<meta property="og:title" content={title} />
	<meta property="og:description" content={description} />
	<meta property="og:image" content={ogImage} />
	<meta property="og:image:width" content="1200" />
	<meta property="og:image:height" content="630" />
	<meta property="og:image:type" content="image/png" />
	<meta property="og:image:alt" content={description} />
	<meta property="og:url" content={shareUrl} />
	<meta name="twitter:card" content="summary_large_image" />
	<meta name="twitter:title" content={title} />
	<meta name="twitter:description" content={description} />
	<meta name="twitter:image" content={ogImage} />
	<meta name="twitter:image:alt" content={description} />
</svelte:head>

<Dashboard {data} />
