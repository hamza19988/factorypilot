# AMD benchmark results

Run `bash infra/amd/bench.sh` inside the droplet's vLLM container, then copy the JSON files here:

```bash
scp root@<droplet-ip>:/tmp/bench/*.json docs/benchmarks/
```

The app's **System** tab reads every `*.json` in this folder (vLLM `bench serve --save-result` format)
and shows output tokens/s, request rate and TTFT per concurrency level. No numbers are shipped in the
repo on purpose: the table only ever shows results you measured on your own AMD GPU.
