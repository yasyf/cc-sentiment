import { DateTime } from 'luxon';
import type { TimelinePoint } from './types.js';

export type DayPartKey = 'late' | 'morning' | 'afternoon' | 'evening';

export interface DayPart {
	key: DayPartKey;
	label: string;
	start: number;
	end: number;
	mid: number;
}

export const DAY_PARTS: readonly DayPart[] = [
	{ key: 'late', label: 'late', start: 22, end: 6, mid: 2 },
	{ key: 'morning', label: 'morning', start: 6, end: 12, mid: 9 },
	{ key: 'afternoon', label: 'afternoon', start: 12, end: 18, mid: 15 },
	{ key: 'evening', label: 'evening', start: 18, end: 22, mid: 20 }
] as const;

export function dayPartFor(hour: number): DayPart {
	for (const part of DAY_PARTS) {
		const hit = part.start < part.end
			? hour >= part.start && hour < part.end
			: hour >= part.start || hour < part.end;
		if (hit) return part;
	}
	throw new Error(`no day part for hour ${hour}`);
}

export interface DayPartBucket {
	time: string;
	date: string;
	part: DayPartKey;
	label: string;
	avg_score: number;
	count: number;
	avg_read_edit_ratio: number | null;
	avg_edits_without_prior_read_ratio: number | null;
	avg_tool_calls_per_turn: number | null;
}

interface Accumulator {
	score: number;
	count: number;
	readEditSum: number;
	readEditCount: number;
	editsWithoutReadSum: number;
	editsWithoutReadCount: number;
	toolCallsSum: number;
	toolCallsCount: number;
}

function fresh(): Accumulator {
	return {
		score: 0,
		count: 0,
		readEditSum: 0,
		readEditCount: 0,
		editsWithoutReadSum: 0,
		editsWithoutReadCount: 0,
		toolCallsSum: 0,
		toolCallsCount: 0
	};
}

export function bucketByDayPart(points: TimelinePoint[], zone: string): DayPartBucket[] {
	const acc = new Map<string, { date: string; part: DayPart; data: Accumulator }>();
	for (const point of points) {
		const dt = DateTime.fromISO(point.time, { zone });
		if (!dt.isValid) continue;
		const part = dayPartFor(dt.hour);
		const anchor = part.key === 'late' && dt.hour < 6 ? dt.minus({ days: 1 }) : dt;
		const date = anchor.toISODate();
		if (!date) continue;
		const key = `${date}::${part.key}`;
		const entry = acc.get(key) ?? { date, part, data: fresh() };
		entry.data.score += point.avg_score * point.count;
		entry.data.count += point.count;
		if (point.avg_read_edit_ratio != null) {
			entry.data.readEditSum += point.avg_read_edit_ratio * point.count;
			entry.data.readEditCount += point.count;
		}
		if (point.avg_edits_without_prior_read_ratio != null) {
			entry.data.editsWithoutReadSum += point.avg_edits_without_prior_read_ratio * point.count;
			entry.data.editsWithoutReadCount += point.count;
		}
		if (point.avg_tool_calls_per_turn != null) {
			entry.data.toolCallsSum += point.avg_tool_calls_per_turn * point.count;
			entry.data.toolCallsCount += point.count;
		}
		acc.set(key, entry);
	}
	return Array.from(acc.values())
		.map(({ date, part, data }) => {
			const base = DateTime.fromISO(date, { zone });
			const ts = part.key === 'late'
				? base.plus({ days: 1 }).set({ hour: part.mid, minute: 0, second: 0, millisecond: 0 })
				: base.set({ hour: part.mid, minute: 0, second: 0, millisecond: 0 });
			return {
				time: ts.toISO() ?? '',
				date,
				part: part.key,
				label: part.label,
				avg_score: data.count > 0 ? data.score / data.count : 0,
				count: data.count,
				avg_read_edit_ratio: data.readEditCount > 0 ? data.readEditSum / data.readEditCount : null,
				avg_edits_without_prior_read_ratio:
					data.editsWithoutReadCount > 0 ? data.editsWithoutReadSum / data.editsWithoutReadCount : null,
				avg_tool_calls_per_turn: data.toolCallsCount > 0 ? data.toolCallsSum / data.toolCallsCount : null
			};
		})
		.toSorted((a, b) => new Date(a.time).getTime() - new Date(b.time).getTime());
}

export function dayBoundaryAnnotations(points: { time: string }[], zone: string): Record<string, object> {
	if (points.length === 0) return {};
	const first = DateTime.fromISO(points[0].time, { zone });
	const last = DateTime.fromISO(points[points.length - 1].time, { zone });
	if (!first.isValid || !last.isValid) return {};
	const annotations: Record<string, object> = {};
	let cursor = first.startOf('day').plus({ days: 1 });
	while (cursor <= last) {
		const iso = cursor.toISO();
		if (iso) {
			annotations[`day-${cursor.toISODate()}`] = {
				type: 'line',
				xMin: iso,
				xMax: iso,
				borderColor: 'rgba(0, 0, 0, 0.08)',
				borderWidth: 1,
				borderDash: [2, 4],
				label: { display: false }
			};
		}
		cursor = cursor.plus({ days: 1 });
	}
	return annotations;
}
