// Ported from Liquid Glass Studio (https://github.com/iyinchao/liquid-glass-studio).
// Copyright 2024 Charles Yin. MIT License; see the upstream repository.

Texture2D<float4> sourceTexture : register(t0);
SamplerState linearClamp : register(s0);
float4 ps_copy(float4 position : SV_Position, float2 uv : TEXCOORD0) : SV_Target {
    return sourceTexture.Sample(linearClamp, uv);
}
