{{- define "shop.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "shop.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := include "shop.name" . -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{/* Labels shared by every object. app= satisfies the namespace-label rule
     spirit and gives Prometheus something stable to select on in Part 5. */}}
{{- define "shop.labels" -}}
app: {{ include "shop.name" . }}
app.kubernetes.io/name: {{ include "shop.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/* Per-component selector labels. Pass a dict {root, component}. */}}
{{- define "shop.selectorLabels" -}}
app: {{ include "shop.name" .root }}
app.kubernetes.io/name: {{ include "shop.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
  Pod-spreading block for one component. Pass a dict {root, component}.
  Renders either a podAntiAffinity (under affinity:) or a
  topologySpreadConstraints block, chosen by .Values.spread.mechanism, so the
  running example commits to exactly one spreading mechanism in values.yaml.

  The output is written at column 0; the caller places it in the pod spec with
  `nindent 6`, which shifts the whole block (labels included) to the right depth.
*/}}
{{- define "shop.spread" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $s := $root.Values.spread -}}
{{- if eq $s.mechanism "podAntiAffinity" -}}
affinity:
  podAntiAffinity:
    {{- if $s.required }}
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: {{ $s.topologyKey }}
        labelSelector:
          matchLabels:
            {{- include "shop.selectorLabels" (dict "root" $root "component" $component) | nindent 12 }}
    {{- else }}
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: {{ $s.topologyKey }}
          labelSelector:
            matchLabels:
              {{- include "shop.selectorLabels" (dict "root" $root "component" $component) | nindent 14 }}
    {{- end }}
{{- else if eq $s.mechanism "topologySpreadConstraints" -}}
topologySpreadConstraints:
  - maxSkew: {{ $s.maxSkew }}
    topologyKey: {{ $s.topologyKey }}
    whenUnsatisfiable: {{ $s.whenUnsatisfiable }}
    labelSelector:
      matchLabels:
        {{- include "shop.selectorLabels" (dict "root" $root "component" $component) | nindent 8 }}
{{- else -}}
{{- fail (printf "spread.mechanism must be podAntiAffinity or topologySpreadConstraints, got %q" $s.mechanism) -}}
{{- end -}}
{{- end -}}
