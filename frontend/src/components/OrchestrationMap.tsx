import { AlertTriangle, BookOpen, FileText, GitBranch, Sparkles, Users } from 'lucide-react'
import { Sparky } from './Sparky'

/**
 * The sign-in screen's product diagram: what flows into IIM's AI core, what comes out, and who
 * it lands on.
 *
 * Drawn as one SVG with a fixed viewBox rather than absolutely-positioned HTML. Earlier passes
 * placed labels with CSS percentages, which can't account for one label being much wider than
 * its neighbour — labels collided at some widths and not others. Here every box is measured (an
 * estimate from the character count, which is exact enough at these sizes) and positioned in the
 * same coordinate space as the lines that point at it, so the layout holds at every scale.
 *
 * The line styling carries meaning, and is worth preserving if this is edited: dashed lines point
 * INWARD (things IIM consumes), solid arrows point OUTWARD (what it produces, and who receives it).
 */

const VB_W = 680
const VB_H = 580
const CX = 340
const CY = 285
/** Nodes sit on an ellipse, wider than tall — it fills a landscape panel better than a circle. */
const RX = 215
const RY = 180

const CORE_R = 70 // dark disc the core sits in
const ORB_R = 44
const RING_A = 88
const RING_B = 112
const SPOKE_START = 120 // just outside the outer ring

const ICON_R = 18
const ICON_GAP = 14
const PILL_H = 34

/** Rough advance width per character. Only used to size the pill behind centred text, so being a
 * point or two off just changes the padding — it can't break the layout. */
const CHAR_W = 6.5
const CHIP_CHAR_W = 5.9

const pillWidth = (label: string) => label.length * CHAR_W + 26
const chipWidth = (label: string) => label.length * CHIP_CHAR_W + 22

type NodeKind = 'input' | 'output' | 'hero' | 'people'

type MapNode = {
  angle: number
  icon: typeof AlertTriangle
  label: string
  sub?: string
  kind: NodeKind
}

const NODES: MapNode[] = [
  {
    angle: -90,
    icon: AlertTriangle,
    label: 'Alerts & Incidents',
    sub: 'CloudWatch · New Relic',
    kind: 'input',
  },
  { angle: -30, icon: Sparkles, label: 'Root cause', sub: 'evidence-cited', kind: 'hero' },
  { angle: 30, icon: FileText, label: 'Daily digest', sub: 'Slack-ready', kind: 'output' },
  { angle: 90, icon: GitBranch, label: 'Tickets', sub: 'Azure DevOps', kind: 'output' },
  { angle: 150, icon: BookOpen, label: 'Runbooks & KB', sub: 'RAG-grounded', kind: 'input' },
  { angle: -150, icon: Users, label: 'SREs & On-call', kind: 'people' },
]

/** Not built yet — drawn detached and dashed, the way the rest of the diagram marks things it
 * only consumes rather than owns, so the picture doesn't over-promise. */
const ROADMAP = [
  { label: 'Next · Cost optimization', x: 120, y: 40 },
  { label: 'Next · Terraform IaC', x: 566, y: 522 },
]

const PILL_STYLES: Record<NodeKind, { fill: string; stroke: string; dash?: string; text: string }> =
  {
    hero: {
      fill: 'url(#om-hero)',
      stroke: 'var(--login-accent)',
      text: '#ffffff',
    },
    output: {
      fill: 'color-mix(in srgb, var(--login-accent) 16%, transparent)',
      stroke: 'color-mix(in srgb, var(--login-accent) 55%, transparent)',
      text: '#ff9a9d',
    },
    input: {
      fill: 'rgba(255,255,255,0.03)',
      stroke: 'var(--login-hair)',
      dash: '4 4',
      text: 'var(--login-text-dim)',
    },
    people: {
      fill: 'rgba(255,255,255,0.07)',
      stroke: 'var(--login-hair)',
      text: 'var(--login-text)',
    },
  }

const SPOKE_STYLES: Record<NodeKind, { stroke: string; dash?: string; width: number }> = {
  hero: { stroke: 'color-mix(in srgb, var(--login-accent) 70%, transparent)', width: 1.4 },
  output: { stroke: 'color-mix(in srgb, var(--login-accent) 45%, transparent)', width: 1.2 },
  input: { stroke: 'var(--login-hair)', dash: '4 5', width: 1.2 },
  people: { stroke: 'rgba(255,255,255,0.22)', width: 1.2 },
}

type Placed = MapNode & {
  /** Icon centre — the point actually sitting on the ellipse. */
  ix: number
  iy: number
  /** Unit vector, centre -> node. */
  ux: number
  uy: number
  /** Pill box. */
  px: number
  py: number
  pw: number
  /** Sub-label baseline. */
  sx: number
  sy: number
}

function place(node: MapNode): Placed {
  const rad = (node.angle * Math.PI) / 180
  const ix = CX + RX * Math.cos(rad)
  const iy = CY + RY * Math.sin(rad)
  const dx = ix - CX
  const dy = iy - CY
  const len = Math.hypot(dx, dy)
  const ux = dx / len
  const uy = dy / len

  const pw = pillWidth(node.label)
  const vertical = Math.abs(uy) > Math.abs(ux)
  // The pill sits beyond the icon, along the same ray — so its wide side always points away from
  // the core (and from its neighbours), never back across the diagram.
  const along = ICON_R + ICON_GAP + (vertical ? PILL_H / 2 : pw / 2)
  const px = ix + ux * along
  const py = iy + uy * along

  // A vertical node's sub-label continues outward (below/above the pill); a side node's would
  // drift diagonally off the edge, so it just tucks under its own pill instead.
  const sx = vertical ? px : px
  const sy = vertical ? py + uy * (PILL_H / 2 + 12) : py + PILL_H / 2 + 12

  return { ...node, ix, iy, ux, uy, px, py, pw, sx, sy }
}

const PLACED = NODES.map(place)

function Pill({
  cx,
  cy,
  w,
  label,
  kind,
}: {
  cx: number
  cy: number
  w: number
  label: string
  kind: NodeKind
}) {
  const s = PILL_STYLES[kind]
  return (
    <>
      <rect
        x={cx - w / 2}
        y={cy - PILL_H / 2}
        width={w}
        height={PILL_H}
        rx={PILL_H / 2}
        fill={s.fill}
        stroke={s.stroke}
        strokeWidth={1}
        strokeDasharray={s.dash}
      />
      <text
        x={cx}
        y={cy}
        dy="0.35em"
        textAnchor="middle"
        fontSize={12.5}
        fontWeight={600}
        fill={s.text}
      >
        {label}
      </text>
    </>
  )
}

function Spoke({ node }: { node: Placed }) {
  const { ux, uy, kind } = node
  const s = SPOKE_STYLES[kind]
  const near = { x: CX + ux * SPOKE_START, y: CY + uy * SPOKE_START }
  const far = { x: node.ix - ux * (ICON_R + 7), y: node.iy - uy * (ICON_R + 7) }
  // Inputs flow toward the core, everything else radiates out of it — so the arrow sits at the
  // opposite end for the two directions.
  const inbound = kind === 'input'
  return (
    <line
      x1={inbound ? far.x : near.x}
      y1={inbound ? far.y : near.y}
      x2={inbound ? near.x : far.x}
      y2={inbound ? near.y : far.y}
      stroke={s.stroke}
      strokeWidth={s.width}
      strokeDasharray={s.dash}
      strokeLinecap="round"
      markerEnd={inbound ? 'url(#om-arrow-dim)' : 'url(#om-arrow)'}
    />
  )
}

function NodeGroup({ node }: { node: Placed }) {
  const Icon = node.icon
  const accent = node.kind === 'input' || node.kind === 'people'
  return (
    <>
      <circle
        cx={node.ix}
        cy={node.iy}
        r={ICON_R}
        fill="var(--login-panel)"
        stroke={
          accent ? 'var(--login-hair)' : 'color-mix(in srgb, var(--login-accent) 45%, transparent)'
        }
        strokeWidth={1}
      />
      <g transform={`translate(${node.ix - 8}, ${node.iy - 8})`}>
        <Icon
          size={16}
          color={accent ? 'var(--login-text-dim)' : 'var(--login-accent)'}
          strokeWidth={1.9}
        />
      </g>
      <Pill cx={node.px} cy={node.py} w={node.pw} label={node.label} kind={node.kind} />
      {node.sub && (
        <text
          x={node.sx}
          y={node.sy}
          dy="0.35em"
          textAnchor="middle"
          fontSize={10}
          fontWeight={500}
          letterSpacing={0.2}
          fill="var(--login-text-dim)"
        >
          {node.sub}
        </text>
      )}
    </>
  )
}

export function OrchestrationMap() {
  return (
    <svg
      viewBox={`0 0 ${VB_W} ${VB_H}`}
      className="h-auto w-full"
      role="img"
      aria-label="IIM's AI core takes in CloudWatch and New Relic alerts plus your runbooks, and produces a cited root cause, an Azure DevOps ticket and a daily digest for SREs and on-call engineers. Cost optimization and Terraform IaC are planned."
    >
      <defs>
        <radialGradient id="om-orb" cx="34%" cy="28%" r="78%">
          <stop offset="0%" stopColor="#ff8f93" />
          <stop offset="45%" stopColor="var(--login-accent)" />
          <stop offset="100%" stopColor="var(--login-accent-strong)" />
        </radialGradient>
        <radialGradient id="om-gloss" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#ffffff" stopOpacity={0.6} />
          <stop offset="100%" stopColor="#ffffff" stopOpacity={0} />
        </radialGradient>
        <radialGradient id="om-bloom" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="var(--login-accent)" stopOpacity={0.35} />
          <stop offset="60%" stopColor="var(--login-accent)" stopOpacity={0.08} />
          <stop offset="100%" stopColor="var(--login-accent)" stopOpacity={0} />
        </radialGradient>
        <linearGradient id="om-hero" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0%" stopColor="var(--login-accent)" />
          <stop offset="100%" stopColor="var(--login-accent-strong)" />
        </linearGradient>
        <marker id="om-arrow" viewBox="0 0 8 8" refX={6} refY={4} markerWidth={5} markerHeight={5} orient="auto-start-reverse">
          <path d="M0.5 1 L6.5 4 L0.5 7 z" fill="color-mix(in srgb, var(--login-accent) 70%, transparent)" />
        </marker>
        <marker id="om-arrow-dim" viewBox="0 0 8 8" refX={6} refY={4} markerWidth={5} markerHeight={5} orient="auto-start-reverse">
          <path d="M0.5 1 L6.5 4 L0.5 7 z" fill="rgba(255,255,255,0.3)" />
        </marker>
      </defs>

      {/* Bloom behind the core. */}
      <circle cx={CX} cy={CY} r={165} fill="url(#om-bloom)" />

      {/* Guide rings — a slow contra-rotation of the dashes is the only motion in the diagram. */}
      <circle
        className="om-ring-a"
        cx={CX}
        cy={CY}
        r={RING_A}
        fill="none"
        stroke="var(--login-hair)"
        strokeWidth={1}
        strokeDasharray="3 6"
      />
      <circle
        className="om-ring-b"
        cx={CX}
        cy={CY}
        r={RING_B}
        fill="none"
        stroke="rgba(255,255,255,0.07)"
        strokeWidth={1}
        strokeDasharray="2 8"
      />

      {PLACED.map((n) => (
        <Spoke key={`spoke-${n.label}`} node={n} />
      ))}

      {/* Core: a dark disc, the glossy orb inside it, and the label straddling its lower edge. */}
      <circle
        cx={CX}
        cy={CY}
        r={CORE_R}
        fill="var(--login-panel)"
        stroke="rgba(255,255,255,0.07)"
        strokeWidth={1}
      />
      <circle className="om-pulse" cx={CX} cy={CY} r={ORB_R + 6} fill="var(--login-accent)" />
      <g className="om-core">
        <circle cx={CX} cy={CY} r={ORB_R} fill="url(#om-orb)" opacity={0.18} />
        <g transform={`translate(${CX - 34}, ${CY - 34})`}>
          <Sparky size={68} />
        </g>
      </g>
      <Pill cx={CX} cy={CY + CORE_R} w={pillWidth('AI triage')} label="AI triage" kind="hero" />

      {PLACED.map((n) => (
        <NodeGroup key={n.label} node={n} />
      ))}

      {ROADMAP.map((r) => {
        const w = chipWidth(r.label)
        return (
          <g key={r.label}>
            <rect
              x={r.x - w / 2}
              y={r.y - 11}
              width={w}
              height={22}
              rx={11}
              fill="none"
              stroke="var(--login-hair)"
              strokeWidth={1}
              strokeDasharray="3 3"
            />
            <text
              x={r.x}
              y={r.y}
              dy="0.35em"
              textAnchor="middle"
              fontSize={10}
              fontWeight={600}
              letterSpacing={0.3}
              fill="var(--login-text-dim)"
            >
              {r.label}
            </text>
          </g>
        )
      })}
    </svg>
  )
}
