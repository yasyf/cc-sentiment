export interface TimelinePoint {
	time: string;
	avg_score: number;
	count: number;
	avg_read_edit_ratio: number | null;
	avg_edits_without_prior_read_ratio: number | null;
	avg_tool_calls_per_turn: number | null;
}

export interface HourlyPoint {
	hour: number;
	avg_score: number;
	count: number;
}

export interface WeekdayPoint {
	dow: number;
	avg_score: number;
	count: number;
}

export interface DistributionPoint {
	score: number;
	count: number;
}

export interface TrendComparison {
	sentiment_current: number;
	sentiment_previous: number;
	sessions_current: number;
	sessions_previous: number;
	read_edit_current: number | null;
	read_edit_previous: number | null;
}

export interface ModelBreakdown {
	claude_model: string;
	avg_score: number;
	count: number;
	avg_read_edit_ratio: number | null;
	avg_write_edit_ratio: number | null;
	avg_subagent_count: number | null;
}

export interface DataResponse {
	timeline: TimelinePoint[];
	distribution: DistributionPoint[];
	total_records: number;
	total_sessions: number;
	last_updated: string;
	trend: TrendComparison;
	model_breakdown: ModelBreakdown[];
	avg_read_edit_ratio: number | null;
	avg_edits_without_prior_read_ratio: number | null;
	avg_tool_calls_per_turn: number | null;
	avg_write_edit_ratio: number | null;
	avg_subagent_count: number | null;
}

export type ContributorType = 'github' | 'gpg' | 'gist';

export interface ShareRecord {
	id: string;
	contributor_type: ContributorType;
	contributor_id: string;
	avatar_url: string | null;
	created_at: string;
}

export interface MyStatResponse {
	kind: string;
	percentile: number;
	text: string;
	tweet_text: string;
}
