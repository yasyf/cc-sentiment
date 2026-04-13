export interface TimelinePoint {
	time: string;
	avg_score: number;
	count: number;
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

export interface DataResponse {
	timeline: TimelinePoint[];
	hourly: HourlyPoint[];
	weekday: WeekdayPoint[];
	distribution: DistributionPoint[];
	total_records: number;
	total_sessions: number;
	total_contributors: number;
	last_updated: string;
}
