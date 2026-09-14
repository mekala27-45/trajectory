export interface Shape {
  readonly name: string;
  readonly sides: number;
  area(): number;
}

class Rectangle implements Shape {
  readonly name = "rectangle";
  readonly sides = 4;

  constructor(
    private readonly width: number,
    private readonly height: number,
  ) {}

  area(): number {
    return this.width * this.height;
  }
}

class Circle implements Shape {
  readonly name = "circle";
  readonly sides = 0;

  constructor(private readonly radius: number) {}

  area(): number {
    return Math.PI * this.radius * this.radius;
  }
}

const CATALOGUE: ReadonlyMap<string, Shape> = new Map<string, Shape>([
  ["rectangle", new Rectangle(3, 4)],
  ["circle", new Circle(2)],
]);

/** Look up a shape by name. Returns undefined for a name that is not in the catalogue. */
export function findShape(name: string): Shape | undefined {
  return CATALOGUE.get(name);
}

/** Every shape name in the catalogue, in insertion order. */
export function shapeNames(): readonly string[] {
  return [...CATALOGUE.keys()];
}
