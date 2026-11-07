import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";

import { type CustomModelFormat, type ModelInfo, uploadCustomModel } from "../api/client";
import { Button, ErrorNotice, Field, inputClass, Notice, selectClass } from "./ui";

const FORMATS: { value: CustomModelFormat; label: string }[] = [
  { value: "auto", label: "Work it out from the file name (.onnx, .pt, .pth)" },
  { value: "onnx", label: "ONNX (YOLO export)" },
  { value: "ultralytics", label: "YOLO .pt (Ultralytics)" },
  { value: "torchvision", label: "torchvision Faster R-CNN (.pth or .pt)" },
];
const ARCHITECTURES = [
  { value: "", label: "Detect from the weights" },
  { value: "fasterrcnn_resnet50_fpn_v2", label: "Faster R-CNN ResNet-50 FPN v2" },
  { value: "fasterrcnn_resnet50_fpn", label: "Faster R-CNN ResNet-50 FPN" },
  { value: "fasterrcnn_mobilenet_v3_large_fpn", label: "Faster R-CNN MobileNet v3 Large FPN" },
  { value: "fasterrcnn_mobilenet_v3_large_320_fpn", label: "Faster R-CNN MobileNet v3 Large 320 FPN" },
];
const NAME_PATTERN = /^[a-z0-9][a-z0-9_.-]{1,62}$/;
const VERSION_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,39}$/;

/** Which runner a file will use: the chosen format, or the one its extension implies. */
export function runtimeOf(fileName: string, format: CustomModelFormat): Exclude<CustomModelFormat, "auto"> | null {
  if (format !== "auto") return format;
  const extension = fileName.toLowerCase().slice(fileName.lastIndexOf("."));
  return extension === ".onnx" ? "onnx" : extension === ".pt" ? "ultralytics" : extension === ".pth" ? "torchvision" : null;
}

/** A model name made from a file name: "Best Model (2).onnx" becomes "best-model-2". */
export function suggestName(fileName: string): string {
  const stem = fileName.replace(/\.[^.]+$/, "").toLowerCase();
  const cleaned = stem.replace(/[^a-z0-9_.-]+/g, "-").replace(/^[^a-z0-9]+|-+$/g, "").slice(0, 63);
  return cleaned.length >= 2 ? cleaned : "my-model";
}

/** Upload a detection model. Once accepted it is offered in Photo Analysis and Video Analysis. */
export function CustomModelUpload() {
  const queryClient = useQueryClient();
  const id = useId();
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("v1");
  const [format, setFormat] = useState<CustomModelFormat>("auto");
  const [classes, setClasses] = useState("");
  const [minScore, setMinScore] = useState("");
  const [architecture, setArchitecture] = useState("");
  const [licence, setLicence] = useState("");
  const [trust, setTrust] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [added, setAdded] = useState<ModelInfo | null>(null);

  const runtime = file ? runtimeOf(file.name, format) : null;
  const score = minScore.trim() === "" ? undefined : Number(minScore);
  const scoreError = score !== undefined && !(score >= 0.01 && score <= 0.99) ? "Use a number from 0.01 to 0.99." : null;
  const nameError = name && !NAME_PATTERN.test(name) ? "2 to 63 lower-case letters, digits, '_', '-' or '.'." : null;
  const versionError = version && !VERSION_PATTERN.test(version) ? "Letters, digits, '_', '-' or '.'." : null;
  const fileError = file && !runtime ? "A model file must end in .onnx, .pt or .pth." : null;

  const upload = useMutation({
    mutationFn: () =>
      uploadCustomModel(
        file!,
        {
          name,
          version,
          format,
          classes,
          minScore: score,
          architecture: runtime === "torchvision" ? architecture || undefined : undefined,
          licence,
          trustCode: runtime === "ultralytics" ? trust : undefined,
        },
        setProgress,
      ),
    onMutate: () => {
      setAdded(null);
      setProgress(0);
    },
    onSuccess: (model) => {
      setAdded(model);
      setFile(null);
      setName("");
      setClasses("");
      setMinScore("");
      setArchitecture("");
      setLicence("");
      setTrust(false);
      void queryClient.invalidateQueries({ queryKey: ["health-models"] });
    },
    onSettled: () => setProgress(null),
  });

  const ready =
    file && runtime && name && version && !nameError && !versionError && !scoreError && (runtime !== "ultralytics" || trust);
  const checking = upload.isPending && progress !== null && progress >= 1;

  return (
    <form
      className="space-y-4"
      aria-label="Add your own model"
      onSubmit={(event) => {
        event.preventDefault();
        if (ready && !upload.isPending) upload.mutate();
      }}
    >
      <div className="grid gap-4 md:grid-cols-2">
        <Field
          label="Model file"
          htmlFor={`${id}-file`}
          error={fileError}
          hint="ONNX (.onnx), YOLO (.pt) or torchvision Faster R-CNN (.pth). Up to 2 GB."
        >
          <input
            id={`${id}-file`}
            type="file"
            accept=".onnx,.pt,.pth"
            className={`${inputClass} file:mr-3 file:rounded file:border-0 file:bg-overlay file:px-2 file:py-1 file:text-fg`}
            onChange={(event) => {
              const chosen = event.target.files?.[0] ?? null;
              setFile(chosen);
              setAdded(null);
              upload.reset();
              if (chosen && !name) setName(suggestName(chosen.name));
            }}
          />
        </Field>
        <Field label="Format" htmlFor={`${id}-format`}>
          <select
            id={`${id}-format`}
            className={selectClass}
            value={format}
            onChange={(event) => setFormat(event.target.value as CustomModelFormat)}
          >
            {FORMATS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Name" htmlFor={`${id}-name`} error={nameError} hint="How the model appears in the Model lists.">
          <input
            id={`${id}-name`}
            className={inputClass}
            value={name}
            maxLength={63}
            onChange={(event) => setName(event.target.value.toLowerCase())}
          />
        </Field>
        <Field label="Version" htmlFor={`${id}-version`} error={versionError}>
          <input
            id={`${id}-version`}
            className={inputClass}
            value={version}
            maxLength={40}
            onChange={(event) => setVersion(event.target.value)}
          />
        </Field>
        <Field
          label="Class names"
          htmlFor={`${id}-classes`}
          hint={
            runtime === "torchvision"
              ? "Required (except for a 91-class COCO model): one per line, in training order, without the background class."
              : "Optional: YOLO files usually include them. Type them only to rename the classes (same number, same order)."
          }
        >
          <textarea
            id={`${id}-classes`}
            className={`${inputClass} h-24 py-2`}
            value={classes}
            placeholder={"tank\ntruck\ntent"}
            onChange={(event) => setClasses(event.target.value)}
          />
        </Field>
        <div className="space-y-4">
          <Field
            label="Minimum score"
            htmlFor={`${id}-score`}
            error={scoreError}
            hint={`Detections scoring under this are not stored. Default ${runtime === "torchvision" ? "0.5" : "0.25"}. Not a validated threshold.`}
          >
            <input
              id={`${id}-score`}
              className={inputClass}
              inputMode="decimal"
              value={minScore}
              placeholder={runtime === "torchvision" ? "0.5" : "0.25"}
              onChange={(event) => setMinScore(event.target.value)}
            />
          </Field>
          {runtime === "torchvision" ? (
            <Field label="Architecture" htmlFor={`${id}-architecture`}>
              <select
                id={`${id}-architecture`}
                className={selectClass}
                value={architecture}
                onChange={(event) => setArchitecture(event.target.value)}
              >
                {ARCHITECTURES.map((option) => (
                  <option key={option.value} value={option.value}>
                    {option.label}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
        </div>
        <Field label="Licence (optional)" htmlFor={`${id}-licence`} hint="Who may use this model, and on what terms.">
          <input
            id={`${id}-licence`}
            className={inputClass}
            value={licence}
            maxLength={500}
            onChange={(event) => setLicence(event.target.value)}
          />
        </Field>
      </div>

      {runtime === "ultralytics" ? (
        <Notice tone="warning" title="A .pt file can run code on this computer">
          <p>
            Opening a YOLO .pt file runs Python code stored inside it, so a file from an unknown source could harm this
            computer. Upload it only if you trust where it came from. Exporting it to ONNX first (
            <code>yolo export model=best.pt format=onnx</code>) avoids the risk.
          </p>
          <label className="mt-2 flex items-start gap-2 text-fg">
            <input type="checkbox" checked={trust} onChange={(event) => setTrust(event.target.checked)} className="mt-0.5" />
            I trust the source of this file.
          </label>
        </Notice>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" disabled={!ready || upload.isPending}>
          {upload.isPending ? "Adding…" : "Add model"}
        </Button>
        {upload.isPending ? (
          <p className="text-[13px] text-fg-muted" role="status">
            {checking
              ? "Checking the model: opening it and running it once on a blank image. This can take a minute…"
              : `Uploading ${Math.round((progress ?? 0) * 100)}%`}
          </p>
        ) : null}
      </div>

      {upload.isError ? <ErrorNotice error={upload.error} title="The model was not added" /> : null}
      {added ? (
        <Notice tone="success" title={`Added ${added.name} ${added.version}`}>
          It finds {(added.settings.classes as string[] | undefined)?.length ?? 0} classes. Choose it under Model in Photo
          Analysis or Video Analysis.
        </Notice>
      ) : null}
    </form>
  );
}
