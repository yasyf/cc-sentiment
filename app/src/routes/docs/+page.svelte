<svelte:head>
	<title>Contribute · cc-sentiment</title>
	<meta name="description" content="Add aggregate Claude Code sentiment metrics to the public dashboard. One command, private setup, only numbers are uploaded." />
	<meta property="og:title" content="Contribute · cc-sentiment" />
	<meta property="og:description" content="One command, private setup, only aggregate numbers are uploaded." />
	<meta property="og:image" content="/og" />
</svelte:head>

<div class="min-h-screen bg-bg">
	<header class="border-b border-border">
		<div class="mx-auto flex max-w-5xl items-baseline justify-between px-6 py-5">
			<h1 class="text-lg font-semibold tracking-tight text-text">
				<a href="/" class="hover:text-accent transition-colors">cc-sentiment</a>
			</h1>
			<nav class="flex items-center gap-5 text-sm text-text-muted">
				<a href="/" class="hover:text-text transition-colors">Dashboard</a>
				<a href="https://github.com/yasyf/cc-sentiment" class="hover:text-text transition-colors" target="_blank" rel="noopener">GitHub</a>
			</nav>
		</div>
	</header>

	<div class="mx-auto max-w-2xl px-6 py-10">
		<h2 class="text-3xl font-semibold tracking-tight text-text">Contribute your data</h2>
		<p class="mt-2 text-text-muted">
			One command. cc-sentiment sets up a verification key, scores your sessions,
			and uploads only aggregate numeric metrics to sentiments.cc.
		</p>

		<div class="mt-10 space-y-6">
			<section>
				<h3 class="text-base font-medium text-text">Run it</h3>
				<p class="mt-2 text-sm text-text-muted">
					Requires <a href="https://docs.astral.sh/uv/" class="text-accent hover:text-accent-hover transition-colors" target="_blank" rel="noopener">uv</a>.
				</p>
				<pre class="mt-3 overflow-x-auto rounded border border-border bg-bg-code px-4 py-3 font-mono text-sm text-text-secondary"><code>uvx cc-sentiment</code></pre>
				<p class="mt-2 text-sm text-text-dim">
					On macOS, set it up on a launchd schedule:
				</p>
				<pre class="mt-2 overflow-x-auto rounded border border-border bg-bg-code px-4 py-3 font-mono text-sm text-text-secondary"><code>uv tool install cc-sentiment
cc-sentiment install</code></pre>
				<p class="mt-2 text-sm text-text-dim">
					Runs once a day in the background on macOS. On Linux or Windows, use cron or your platform's scheduler to run <code class="rounded bg-bg-code px-1 py-0.5 font-mono text-xs">cc-sentiment run</code>. Undo the macOS schedule any time with <code class="rounded bg-bg-code px-1 py-0.5 font-mono text-xs">cc-sentiment uninstall</code>.
				</p>
				<p class="mt-3 text-sm text-text-muted">
					The wizard sets up a verification key, scores your sessions with the best available engine,
					and uploads the numeric scores. If anything's missing, it'll guide you through setup.
				</p>
			</section>

			<hr class="border-border" />

			<section>
				<h3 class="text-base font-medium text-text">What happens</h3>
				<ol class="mt-3 space-y-2 pl-5 text-sm text-text-muted list-decimal marker:text-text-dim">
					<li>Finds your Claude Code transcripts in <code class="rounded bg-bg-code px-1 py-0.5 font-mono text-xs">~/.claude/projects/</code></li>
					<li>Scores each conversation locally when the local engine is available, otherwise asks before using the configured fallback engine</li>
					<li>Signs each upload with a key you control and a public verification handle (GitHub username or GPG fingerprint) used only to find a public key and verify signatures</li>
					<li>Uploads only the numeric scores and timestamps to the <a href="/" class="text-accent hover:text-accent-hover transition-colors">dashboard</a></li>
				</ol>
			</section>

			<hr class="border-border" />

			<section>
				<h3 class="text-base font-medium text-text">Privacy</h3>
				<p class="mt-2 text-sm text-text-muted">
					Your conversations are not uploaded to sentiments.cc. The private signing key stays on your machine.
					What gets uploaded: a score from 1-5, a timestamp, and a public verification handle (GitHub username or GPG fingerprint) used only to find a public key and verify signatures.
					What doesn't: conversation text, file contents, file paths, prompts, tool inputs, or tool outputs.
				</p>
			</section>
		</div>
	</div>

	<footer class="border-t border-border">
		<div class="mx-auto max-w-5xl px-6 py-6 text-center text-xs text-text-dim">
			<a href="https://github.com/yasyf/cc-sentiment" class="text-accent hover:text-accent-hover transition-colors" target="_blank" rel="noopener">GitHub</a>
		</div>
	</footer>
</div>
