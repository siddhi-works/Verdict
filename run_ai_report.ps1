# Runs the AI reader over the generated set and the public SLA documents, paced for a free-tier token limit.
# Successful readings are cached in data/v2/ai_cache, so an interrupted run resumes where it stopped.
#   .\run_ai_report.ps1
$env:SLA_AI_MIN_INTERVAL = "16"
$env:SLA_AI_CACHE = "data/v2/ai_cache"
$py = ".\.venv\Scripts\python.exe"

& $py -u -m eval.run_eval --data data/v2 --mode llm --json data/v2/ai_results.json *> data/v2/AI_RESULTS.txt
"eval exit code: $LASTEXITCODE"
& $py -u -m data_real.run_real --mode llm --out data_real/RESULTS_AI.md *> data_real/RESULTS_AI.log
"real-documents exit code: $LASTEXITCODE"
