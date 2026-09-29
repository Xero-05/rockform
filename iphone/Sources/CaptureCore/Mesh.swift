import Foundation

public struct Point3: Codable, Equatable, Sendable {
    public var x: Double
    public var y: Double
    public var z: Double
    public init(_ x: Double, _ y: Double, _ z: Double) { self.x = x; self.y = y; self.z = z }
    public static func + (a: Self, b: Self) -> Self { .init(a.x+b.x, a.y+b.y, a.z+b.z) }
    public static func - (a: Self, b: Self) -> Self { .init(a.x-b.x, a.y-b.y, a.z-b.z) }
    public static func * (a: Self, b: Double) -> Self { .init(a.x*b, a.y*b, a.z*b) }
    public func dot(_ b: Self) -> Double { x*b.x + y*b.y + z*b.z }
    public func cross(_ b: Self) -> Self { .init(y*b.z-z*b.y, z*b.x-x*b.z, x*b.y-y*b.x) }
    public var finite: Bool { x.isFinite && y.isFinite && z.isFinite }
}

public struct Triangle: Codable, Equatable, Sendable {
    public var a: Int; public var b: Int; public var c: Int
    public init(_ a: Int, _ b: Int, _ c: Int) { self.a=a; self.b=b; self.c=c }
    public var indices: [Int] { [a,b,c] }
}

/// Frame axes in ARKit world coordinates (meters). Export vertices use millimeters.
public struct CaptureFrame: Codable, Sendable {
    public var origin: Point3; public var right: Point3; public var up: Point3; public var outward: Point3
    public init(origin: Point3, right: Point3, up: Point3, outward: Point3) {
        self.origin=origin; self.right=right; self.up=up; self.outward=outward
    }
    public static let identity = Self(origin: .init(0,0,0), right: .init(1,0,0), up: .init(0,1,0), outward: .init(0,0,1))
    public func millimeters(from world: Point3) -> Point3 {
        let d = world-origin
        return Point3(d.dot(right), d.dot(up), d.dot(outward))*1000
    }
}

public enum CaptureError: Error, LocalizedError {
    case emptyMesh, invalidMesh, tooLarge, invalidArchivePath, cancelled
    public var errorDescription: String? {
        switch self {
        case .emptyMesh: return "No surface was captured inside the box. Move closer, aim at the rock, and start a new scan."
        case .invalidMesh: return "The captured mesh contains invalid coordinates or triangle indices. Start a new scan."
        case .tooLarge: return "This capture is too large for the lightweight exporter. Scan a smaller patch."
        case .invalidArchivePath: return "An export filename is invalid."
        case .cancelled: return "Export cancelled."
        }
    }
}

public struct SurfaceMesh: Codable, Sendable {
    public var vertices: [Point3]
    public var triangles: [Triangle]
    public init(vertices: [Point3] = [], triangles: [Triangle] = []) { self.vertices=vertices; self.triangles=triangles }
    public func validate() throws {
        guard !vertices.isEmpty, !triangles.isEmpty else { throw CaptureError.emptyMesh }
        guard vertices.count <= 500_000, triangles.count <= 1_000_000 else { throw CaptureError.tooLarge }
        guard vertices.allSatisfy({ $0.finite && abs($0.x)<1e7 && abs($0.y)<1e7 && abs($0.z)<1e7 }),
              triangles.allSatisfy({ t in t.indices.allSatisfy { vertices.indices.contains($0) } && Set(t.indices).count == 3 })
        else { throw CaptureError.invalidMesh }
    }
    public var dimensions: Point3 {
        guard let first=vertices.first else { return .init(0,0,0) }
        var lo=first; var hi=first
        for p in vertices {
            lo = .init(min(lo.x,p.x), min(lo.y,p.y), min(lo.z,p.z))
            hi = .init(max(hi.x,p.x), max(hi.y,p.y), max(hi.z,p.z))
        }
        return hi-lo
    }
    /// Keeps whole triangles inside the capture box. Creates an open scan boundary;
    /// filling, smoothing, and solid backing belong to Rockform on Windows.
    public func cropped(widthMM: Double, depthMM: Double) -> Self {
        let inside=vertices.map { $0.finite && abs($0.x)<=widthMM/2 && abs($0.y)<=widthMM/2 && abs($0.z)<=depthMM/2 }
        let faces=triangles.filter { t in t.indices.allSatisfy { inside.indices.contains($0) && inside[$0] } }
        var mapping: [Int:Int]=[:]; var points: [Point3]=[]; var result: [Triangle]=[]
        for t in faces {
            let mapped=t.indices.map { index -> Int in
                if let value=mapping[index] { return value }
                mapping[index]=points.count; points.append(vertices[index]); return points.count-1
            }
            result.append(.init(mapped[0],mapped[1],mapped[2]))
        }
        return .init(vertices: points, triangles: result)
    }
    public static func merge(_ parts: [Self]) throws -> Self {
        var result=Self()
        for part in parts where !part.triangles.isEmpty {
            try part.validate()
            let offset=result.vertices.count
            guard offset+part.vertices.count<=500_000, result.triangles.count+part.triangles.count<=1_000_000 else { throw CaptureError.tooLarge }
            result.vertices.append(contentsOf: part.vertices)
            result.triangles.append(contentsOf: part.triangles.map { .init($0.a+offset,$0.b+offset,$0.c+offset) })
        }
        try result.validate()
        return result
    }
    public func obj(isDemo: Bool, cancelled: () -> Bool = { false }) throws -> Data {
        try validate()
        var text = "# Rockform Capture 0.1.0\n# units: millimeters\n# source: \(isDemo ? "SYNTHETIC DEMO - NOT A SCAN" : "ARKit LiDAR scene mesh")\n# Open surface; confirm physical reference in Rockform before design.\no rock_surface\n"
        let locale=Locale(identifier: "en_US_POSIX")
        for (i,p) in vertices.enumerated() {
            if i%4096 == 0 && cancelled() { throw CaptureError.cancelled }
            text += String(format: "v %.5f %.5f %.5f\n", locale: locale, p.x,p.y,p.z)
        }
        for (i,t) in triangles.enumerated() {
            if i%4096 == 0 && cancelled() { throw CaptureError.cancelled }
            text += "f \(t.a+1) \(t.b+1) \(t.c+1)\n"
        }
        return Data(text.utf8)
    }
    public static func demo() -> Self {
        let n=51
        var mesh=Self()
        for row in 0..<n { for col in 0..<n {
            let x=Double(col)*6-150, y=Double(row)*6-150
            let mound=48*exp(-(x*x/3600+y*y/1600))
            let ledge=12*exp(-pow((y+15)/10,2))*exp(-pow(x/90,4))
            mesh.vertices.append(.init(x,y,mound+ledge+2*sin(x/21)*cos(y/17)))
        }}
        for row in 0..<(n-1) { for col in 0..<(n-1) {
            let a=row*n+col
            mesh.triangles.append(.init(a,a+1,a+n+1)); mesh.triangles.append(.init(a,a+n+1,a+n))
        }}
        return mesh
    }
}
