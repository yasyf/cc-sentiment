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

export const DAY_PART_EMOJI: Record<DayPartKey, string> = {
	morning: '🌅',
	afternoon: '☀️',
	evening: '🌆',
	late: '🌙'
};

export const DAY_PART_TINT: Record<DayPartKey, string> = {
	morning: 'rgba(251, 191, 36, 0.06)',
	afternoon: 'rgba(251, 146, 60, 0.05)',
	evening: 'rgba(167, 139, 250, 0.06)',
	late: 'rgba(99, 102, 241, 0.07)'
};

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
				borderColor: 'rgba(0, 0, 0, 0.18)',
				borderWidth: 1.5,
				label: { display: false }
			};
		}
		cursor = cursor.plus({ days: 1 });
	}
	return annotations;
}

export function dayPartBandAnnotations(points: { time: string }[], zone: string): Record<string, object> {
	if (points.length === 0) return {};
	const first = DateTime.fromISO(points[0].time, { zone });
	const last = DateTime.fromISO(points[points.length - 1].time, { zone });
	if (!first.isValid || !last.isValid) return {};
	const annotations: Record<string, object> = {};
	let cursor = first.startOf('day').minus({ days: 1 });
	const limit = last.endOf('day');
	while (cursor <= limit) {
		const date = cursor.toISODate();
		if (!date) {
			cursor = cursor.plus({ days: 1 });
			continue;
		}
		for (const part of DAY_PARTS) {
			const xMin = cursor.set({ hour: part.start, minute: 0, second: 0, millisecond: 0 });
			const xMax = part.start < part.end
				? cursor.set({ hour: part.end, minute: 0, second: 0, millisecond: 0 })
				: cursor.plus({ days: 1 }).set({ hour: part.end, minute: 0, second: 0, millisecond: 0 });
			const xMinIso = xMin.toISO();
			const xMaxIso = xMax.toISO();
			if (!xMinIso || !xMaxIso) continue;
			annotations[`band-${date}-${part.key}`] = {
				type: 'box',
				xMin: xMinIso,
				xMax: xMaxIso,
				backgroundColor: DAY_PART_TINT[part.key],
				borderWidth: 0,
				drawTime: 'beforeDatasetsDraw'
			};
		}
		cursor = cursor.plus({ days: 1 });
	}
	return annotations;
}
