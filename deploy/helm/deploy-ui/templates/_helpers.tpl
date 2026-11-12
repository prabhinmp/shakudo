{{- define "deploy-ui.name" -}}deploy-ui{{- end -}}
{{- define "deploy-ui.fullname" -}}{{ .Release.Name }}-{{ include "deploy-ui.name" . }}{{- end -}}
{{- define "deploy-ui.labels" -}}
app.kubernetes.io/name: {{ include "deploy-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}
{{- define "deploy-ui.selectorLabels" -}}
app.kubernetes.io/name: {{ include "deploy-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
{{- define "deploy-ui.backendFqdn" -}}
{{- if .Values.backend.namespace -}}{{ .Values.backend.host }}.{{ .Values.backend.namespace }}.svc.cluster.local{{- else -}}{{ .Values.backend.host }}.{{ .Release.Namespace }}.svc.cluster.local{{- end -}}
{{- end -}}
