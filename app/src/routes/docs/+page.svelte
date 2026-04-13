<script lang="ts">
	const API_BASE = 'https://anetaco--cc-sentiment-api-serve.modal.run';

	const endpoints = [
		{
			method: 'GET',
			path: '/data',
			description: 'Returns aggregated sentiment data including timeline, hourly breakdown, weekday averages, and score distribution.',
			params: [{ name: 'days', type: 'int', default: '7', description: 'Number of days to query (1-365)' }],
			response: `{
  "timeline": [{ "time": "2026-04-12T07:00:00Z", "avg_score": 3.07, "count": 14 }],
  "hourly": [{ "hour": 9, "avg_score": 3.5, "count": 42 }],
  "weekday": [{ "dow": 1, "avg_score": 2.8, "count": 55 }],
  "distribution": [{ "score": 3, "count": 115 }],
  "total_records": 277,
  "last_updated": "2026-04-13T13:49:59Z"
}`
		},
		{
			method: 'POST',
			path: '/upload',
			description: 'Upload signed sentiment records. Requires a valid SSH or GPG signature from a GitHub user.',
			params: [],
			response: `{ "ingested": 50 }`
		},
		{
			method: 'POST',
			path: '/verify',
			description: 'Verify signing credentials without uploading data. Used by the client to test key setup.',
			params: [],
			response: `{ "status": "ok" }`
		}
	];
</script>

<svelte:head>
	<title>API Docs - cc-sentiment</title>
</svelte:head>

<div class="min-h-screen bg-bg px-4 py-8 sm:px-6">
	<div class="mx-auto max-w-4xl">
		<header class="mb-8">
			<div class="flex items-baseline gap-4">
				<a href="/" class="text-accent hover:text-accent-hover transition-colors text-sm">&larr; Dashboard</a>
			</div>
			<h1 class="mt-4 text-2xl font-semibold text-text">API Documentation</h1>
			<p class="mt-2 text-sm text-text-muted">
				Base URL: <code class="rounded bg-bg-card px-2 py-0.5 font-mono text-accent">{API_BASE}</code>
			</p>
		</header>

		<section class="mb-8 rounded-xl border border-border bg-bg-card p-6">
			<h2 class="text-lg font-semibold text-text">Getting Started</h2>
			<div class="mt-4 space-y-3 text-sm text-text-muted">
				<p>
					<strong class="text-text">1. Install the CLI</strong><br />
					<code class="mt-1 inline-block rounded bg-bg px-2 py-1 font-mono text-xs">pip install cc-sentiment</code>
				</p>
				<p>
					<strong class="text-text">2. Set up signing keys</strong><br />
					<code class="mt-1 inline-block rounded bg-bg px-2 py-1 font-mono text-xs">cc-sentiment setup</code>
				</p>
				<p>
					<strong class="text-text">3. Scan & upload</strong><br />
					<code class="mt-1 inline-block rounded bg-bg px-2 py-1 font-mono text-xs">cc-sentiment scan --upload</code>
				</p>
			</div>
		</section>

		<section class="space-y-6">
			<h2 class="text-lg font-semibold text-text">Endpoints</h2>

			{#each endpoints as endpoint}
				<div class="rounded-xl border border-border bg-bg-card p-6">
					<div class="flex items-center gap-3">
						<span
							class="rounded px-2 py-0.5 text-xs font-bold uppercase {endpoint.method === 'GET'
								? 'bg-sentiment-5/20 text-sentiment-5'
								: 'bg-accent/20 text-accent'}"
						>
							{endpoint.method}
						</span>
						<code class="font-mono text-sm text-text">{endpoint.path}</code>
					</div>
					<p class="mt-3 text-sm text-text-muted">{endpoint.description}</p>

					{#if endpoint.params.length > 0}
						<div class="mt-4">
							<h4 class="text-xs font-semibold uppercase tracking-wider text-text-dim">Parameters</h4>
							<div class="mt-2 space-y-2">
								{#each endpoint.params as param}
									<div class="flex items-baseline gap-2 text-sm">
										<code class="font-mono text-accent">{param.name}</code>
										<span class="text-text-dim">({param.type})</span>
										<span class="text-text-muted">- {param.description}</span>
										<span class="text-text-dim">Default: {param.default}</span>
									</div>
								{/each}
							</div>
						</div>
					{/if}

					<div class="mt-4">
						<h4 class="text-xs font-semibold uppercase tracking-wider text-text-dim">Response</h4>
						<pre class="mt-2 overflow-x-auto rounded-lg bg-bg p-3 text-xs text-text-muted"><code>{endpoint.response}</code></pre>
					</div>
				</div>
			{/each}
		</section>

		<section class="mt-8 rounded-xl border border-border bg-bg-card p-6">
			<h2 class="text-lg font-semibold text-text">Rate Limits</h2>
			<div class="mt-3 space-y-2 text-sm text-text-muted">
				<div class="flex justify-between border-b border-border pb-2">
					<span>GET /data</span>
					<span class="font-mono text-text-dim">120 req/min</span>
				</div>
				<div class="flex justify-between border-b border-border pb-2">
					<span>POST /upload</span>
					<span class="font-mono text-text-dim">100 req/min</span>
				</div>
				<div class="flex justify-between">
					<span>POST /verify</span>
					<span class="font-mono text-text-dim">10 req/min</span>
				</div>
			</div>
		</section>

		<section class="mt-8 rounded-xl border border-border bg-bg-card p-6">
			<h2 class="text-lg font-semibold text-text">Authentication</h2>
			<p class="mt-3 text-sm text-text-muted">
				Data uploads are authenticated via cryptographic signatures. The client signs payloads using either
				SSH keys or GPG keys registered on GitHub. The server verifies signatures against the user's
				public keys fetched from <code class="font-mono text-accent">github.com/&lt;username&gt;.keys</code> or
				<code class="font-mono text-accent">github.com/&lt;username&gt;.gpg</code>.
			</p>
			<p class="mt-2 text-sm text-text-muted">
				The <code class="font-mono text-accent">GET /data</code> endpoint requires a bearer token via the
				<code class="font-mono text-accent">Authorization</code> header. The dashboard fetches data server-side
				using a shared secret that is never exposed to the browser.
			</p>
		</section>

		<footer class="mt-12 border-t border-border pt-6 pb-8 text-center text-xs text-text-dim">
			<a href="https://github.com/yasyf/cc-sentiment" class="text-accent hover:text-accent-hover transition-colors" target="_blank" rel="noopener">
				GitHub
			</a>
		</footer>
	</div>
</div>
