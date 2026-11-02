; Immutable thread-local objects may carry addresses of resource-array elements
; into an internal helper. A lost alias must not turn live texture reads to zero.
%Env = type { ptr, ptr }
%Envs = type { [2 x %Env] }
%Texture = type { ptr addrspace(1) }
%Sampler = type { ptr addrspace(2) }

define <4 x float> @local_textures(ptr byval([2 x ptr addrspace(1)]) %textures, ptr byval([2 x ptr addrspace(2)]) %samplers, <2 x float> %uv, i32 %page) {
entry:
  %envs = alloca %Envs, align 8
  %t0 = bitcast ptr %envs to ptr
  store ptr %textures, ptr %t0, align 8
  %s0 = getelementptr inbounds %Envs, ptr %envs, i64 0, i32 0, i64 0, i32 1
  store ptr %samplers, ptr %s0, align 8
  %t1 = getelementptr inbounds [2 x ptr addrspace(1)], ptr %textures, i64 0, i64 1
  %tf1 = getelementptr inbounds %Envs, ptr %envs, i64 0, i32 0, i64 1, i32 0
  store ptr %t1, ptr %tf1, align 8
  %s1 = getelementptr inbounds [2 x ptr addrspace(2)], ptr %samplers, i64 0, i64 1
  %sf1 = getelementptr inbounds %Envs, ptr %envs, i64 0, i32 0, i64 1, i32 1
  store ptr %s1, ptr %sf1, align 8
  %color = call <4 x float> @read_env(ptr %envs, <2 x float> %uv, i32 %page)
  ret <4 x float> %color
}

define internal <4 x float> @read_env(ptr %envs, <2 x float> %uv, i32 %page) {
entry:
  %first = icmp eq i32 %page, 0
  br i1 %first, label %zero, label %one
zero:
  %e0 = getelementptr inbounds %Envs, ptr %envs, i64 0, i32 0, i64 0
  %tf0 = getelementptr inbounds %Env, ptr %e0, i64 0, i32 0
  %tp0 = load ptr, ptr %tf0, align 8
  %sp0f = getelementptr inbounds %Env, ptr %e0, i64 0, i32 1
  %sp0 = load ptr, ptr %sp0f, align 8
  %tw0 = getelementptr inbounds %Texture, ptr %tp0, i64 0, i32 0
  %tex0 = load ptr addrspace(1), ptr %tw0, align 8
  %sw0 = getelementptr inbounds %Sampler, ptr %sp0, i64 0, i32 0
  %samp0 = load ptr addrspace(2), ptr %sw0, align 8
  %r0 = call { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1) %tex0, ptr addrspace(2) %samp0, <2 x float> %uv, i1 true, <2 x i32> zeroinitializer, i1 false, float 0.0, float 0.0, i32 0)
  %c0 = extractvalue { <4 x float>, i8 } %r0, 0
  ret <4 x float> %c0
one:
  %e1 = getelementptr inbounds %Envs, ptr %envs, i64 0, i32 0, i64 1
  %tf = getelementptr inbounds %Env, ptr %e1, i64 0, i32 0
  %tp = load ptr, ptr %tf, align 8
  %sf = getelementptr inbounds %Env, ptr %e1, i64 0, i32 1
  %sp = load ptr, ptr %sf, align 8
  %tw = getelementptr inbounds %Texture, ptr %tp, i64 0, i32 0
  %tex = load ptr addrspace(1), ptr %tw, align 8
  %sw = getelementptr inbounds %Sampler, ptr %sp, i64 0, i32 0
  %samp = load ptr addrspace(2), ptr %sw, align 8
  %r1 = call { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1) %tex, ptr addrspace(2) %samp, <2 x float> %uv, i1 true, <2 x i32> zeroinitializer, i1 false, float 0.0, float 0.0, i32 0)
  %c1 = extractvalue { <4 x float>, i8 } %r1, 0
  ret <4 x float> %c1
}

declare { <4 x float>, i8 } @air.sample_texture_2d.v4f32(ptr addrspace(1), ptr addrspace(2), <2 x float>, i1, <2 x i32>, i1, float, float, i32)
!air.fragment = !{!0}
!0 = !{ptr @local_textures, !1, !2}
!1 = !{!3}
!2 = !{!4, !5, !6, !7}
!3 = !{!"air.render_target", i32 0, i32 0, !"air.arg_type_name", !"float4"}
!4 = !{i32 0, !"air.texture", !"air.location_index", i32 0, i32 2, !"air.sample", !"air.arg_type_name", !"array<texture2d<float, sample>, 2>", !"air.arg_name", !"textures"}
!5 = !{i32 1, !"air.sampler", !"air.location_index", i32 0, i32 2, !"air.arg_type_name", !"array<sampler, 2>", !"air.arg_name", !"samplers"}
!6 = !{i32 2, !"air.fragment_input", !"user(locn0)", !"air.center", !"air.perspective", !"air.arg_type_name", !"float2", !"air.arg_name", !"uv"}
!7 = !{i32 3, !"air.fragment_input", !"user(locn1)", !"air.flat", !"air.arg_type_name", !"int", !"air.arg_name", !"page"}
