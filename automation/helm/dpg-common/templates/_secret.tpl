{{/*
<name>-secrets Secret, loaded into the container with envFrom. Holds the
non-empty secretEnvFromValues entries plus every extraSecrets key. Rendered
when the chart declares either. Values are passed at deploy time with --set,
never stored in a values file.
Usage: templates/secret.yaml: {{ include "dpg-common.secret" . }}
*/}}
{{- define "dpg-common.secret" -}}
{{- if include "dpg-common.hasSecret" . -}}
{{- if .Values.requireOneSecret -}}
{{- $any := false -}}
{{- range .Values.secretEnvFromValues -}}
{{- if include "dpg-common.valueAt" (dict "root" $.Values "path" .key) }}{{ $any = true }}{{ end -}}
{{- end -}}
{{- if not $any -}}
{{- fail (printf "%s needs at least one of: %s" (include "dpg-common.fullname" .) (include "dpg-common.secretKeys" .)) -}}
{{- end -}}
{{- end -}}
apiVersion: v1
kind: Secret
metadata:
  name: {{ include "dpg-common.fullname" . }}-secrets
  namespace: {{ .Release.Namespace }}
  labels:
    app: {{ include "dpg-common.fullname" . }}
type: Opaque
stringData:
  {{- range .Values.secretEnvFromValues }}
  {{- $val := include "dpg-common.valueAt" (dict "root" $.Values "path" .key) }}
  {{- if $val }}
  {{ .name }}: {{ $val | quote }}
  {{- end }}
  {{- end }}
  {{- range $name, $val := .Values.extraSecrets }}
  {{ $name }}: {{ $val | quote }}
  {{- end }}
{{- end -}}
{{- end -}}
