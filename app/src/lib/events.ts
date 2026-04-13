export interface KnownEvent {
	date: string;
	label: string;
	type: 'regression' | 'release' | 'milestone';
}

export const EVENTS: KnownEvent[] = [
	{ date: '2025-02-12', label: 'redact-thinking rollout', type: 'regression' },
	{ date: '2025-03-08', label: 'Thinking >50% redacted', type: 'regression' },
];
