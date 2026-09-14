import { findShape, shapeNames } from "@core/index.js";

export function report(): string[] {
  const lines: string[] = [];
  for (const name of shapeNames()) {
    const shape = findShape(name);
    lines.push(`${shape.name} has ${shape.sides} sides and an area of ${shape.area().toFixed(2)}`);
  }
  return lines;
}

if (process.argv[1]?.endsWith("main.js")) {
  for (const line of report()) {
    console.log(line);
  }
}
