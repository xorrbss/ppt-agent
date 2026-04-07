"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  LabelList,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";

type SimpleBarDatum = {
  label: string;
  value: number;
};

type DualSeriesDatum = {
  label: string;
  valueA: number;
  valueB: number;
};

type PieDatum = {
  name: string;
  value: number;
};

type PieLabelProps = {
  cx?: number;
  cy?: number;
  midAngle?: number;
  innerRadius?: number;
  outerRadius?: number;
  percent?: number;
};

const PRIMARY = "#4d4ef3";
const SECONDARY = "#9fb6ff";
const LIGHT = "#e8eefb";
const GRID = "#8f96aa";

function renderOutsidePieLabel({
  cx = 0,
  cy = 0,
  midAngle = 0,
  outerRadius = 0,
  percent = 0,
}: PieLabelProps) {
  const radius = outerRadius + 18;
  const x = cx + radius * Math.cos((-midAngle * Math.PI) / 180);
  const y = cy + radius * Math.sin((-midAngle * Math.PI) / 180);

  return (
    <text
      x={x}
      y={y}
      fill="#7e8cb6"
      textAnchor={x > cx ? "start" : "end"}
      dominantBaseline="central"
      fontSize="10"
      fontWeight="500"
    >
      {(percent * 100).toFixed(1)}%
    </text>
  );
}

export function CompactBarChart({
  data,
}: {
  data: SimpleBarDatum[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 14, right: 10, left: -12, bottom: 8 }}>
        <CartesianGrid vertical={false} stroke={GRID} strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          tick={{ fill: "#4b5563", fontSize: 9 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#4b5563", fontSize: 9 }}
          tickLine={false}
          axisLine={false}
          width={28}
        />
        <Bar dataKey="value" fill={PRIMARY} radius={[3, 3, 0, 0]} isAnimationActive={false}>
          <LabelList
            dataKey="value"
            position="top"
            fill={PRIMARY}
            fontSize={9}
            offset={4}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function WorkflowBarChart({
  data,
}: {
  data: SimpleBarDatum[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <BarChart data={data} margin={{ top: 18, right: 16, left: 0, bottom: 12 }}>
        <CartesianGrid vertical={false} stroke={GRID} strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          tick={{ fill: "#4b5563", fontSize: 12 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#4b5563", fontSize: 12 }}
          tickLine={false}
          axisLine={false}
          width={40}
        />
        <Bar dataKey="value" fill={PRIMARY} radius={[4, 4, 0, 0]} isAnimationActive={false}>
          <LabelList
            dataKey="value"
            position="top"
            fill={PRIMARY}
            fontSize={11}
            offset={4}
          />
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function TrendLineChart({
  data,
}: {
  data: DualSeriesDatum[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 12, right: 8, left: -16, bottom: 10 }}>
        <CartesianGrid vertical={false} stroke={GRID} strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          tick={{ fill: "#4b5563", fontSize: 8 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#4b5563", fontSize: 8 }}
          tickLine={false}
          axisLine={false}
          width={24}
        />
        <Line
          type="monotone"
          dataKey="valueA"
          stroke={PRIMARY}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="valueB"
          stroke={SECONDARY}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function DualLineChart({
  data,
}: {
  data: DualSeriesDatum[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={data} margin={{ top: 24, right: 18, left: 0, bottom: 24 }}>
        <CartesianGrid vertical={false} stroke={GRID} />
        <XAxis
          dataKey="label"
          tick={{ fill: "#4b5563", fontSize: 12 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#4b5563", fontSize: 12 }}
          tickLine={false}
          axisLine={false}
          width={40}
        />
        <Line
          type="monotone"
          dataKey="valueA"
          stroke={PRIMARY}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          dataKey="valueB"
          stroke={SECONDARY}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function AreaTrendChart({
  data,
  idPrefix,
}: {
  data: SimpleBarDatum[];
  idPrefix: string;
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <AreaChart data={data} margin={{ top: 18, right: 12, left: -12, bottom: 6 }}>
        <defs>
          <linearGradient id={`${idPrefix}-fill`} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={PRIMARY} stopOpacity={0.45} />
            <stop offset="100%" stopColor={PRIMARY} stopOpacity={0.05} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke={GRID} strokeDasharray="3 3" />
        <XAxis
          dataKey="label"
          tick={{ fill: "#4b5563", fontSize: 8 }}
          tickLine={false}
          axisLine={false}
        />
        <YAxis
          tick={{ fill: "#4b5563", fontSize: 8 }}
          tickLine={false}
          axisLine={false}
          width={24}
        />
        <Area
          type="monotone"
          dataKey="value"
          stroke={PRIMARY}
          strokeWidth={2}
          fill={`url(#${idPrefix}-fill)`}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function SemiDonutChart({
  data,
}: {
  data: PieDatum[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          startAngle={180}
          endAngle={0}
          cx="50%"
          cy="92%"
          innerRadius={58}
          outerRadius={88}
          paddingAngle={6}
          stroke="none"
          labelLine={false}
          label={renderOutsidePieLabel}
          isAnimationActive={false}
        >
          {data.map((entry, index) => (
            <Cell
              key={`${entry.name}-${index}`}
              fill={[PRIMARY, SECONDARY, LIGHT][index % 3]}
            />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}

export function CompactPieChart({
  data,
}: {
  data: PieDatum[];
}) {
  return (
    <ResponsiveContainer width="100%" height="100%">
      <PieChart>
        <Pie
          data={data}
          dataKey="value"
          cx="50%"
          cy="50%"
          innerRadius={40}
          outerRadius={76}
          paddingAngle={1}
          stroke="none"
          labelLine={false}
          label={renderOutsidePieLabel}
          isAnimationActive={false}
        >
          {data.map((entry, index) => (
            <Cell
              key={`${entry.name}-${index}`}
              fill={[PRIMARY, SECONDARY, "#d7dff4"][index % 3]}
            />
          ))}
        </Pie>
      </PieChart>
    </ResponsiveContainer>
  );
}
