// Ported from Liquid Glass Studio (https://github.com/iyinchao/liquid-glass-studio).
// Copyright 2024 Charles Yin. MIT License; see the upstream repository.

struct VertexOutput { float4 position : SV_Position; float2 uv : TEXCOORD0; };

VertexOutput vs_main(uint id : SV_VertexID) {
    VertexOutput output;
    float2 position = float2((id << 1) & 2, id & 2);
    output.uv = float2(position.x, 1.0 - position.y);
    output.position = float4(position * float2(2.0, -2.0) + float2(-1.0, 1.0), 0.0, 1.0);
    return output;
}
