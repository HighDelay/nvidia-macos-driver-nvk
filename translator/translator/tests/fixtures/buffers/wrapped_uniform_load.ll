; Compute counterpart of Resolve's struct-wrapped uniform-slot reads.
target triple = "air64_v28-apple-macosx26.7.0"

%struct.GenericUniformHeader = type { { [4 x i32] } }

define void @k(ptr addrspace(1) %out, ptr addrspace(2) readonly %objectHeader) {
entry:
  %flags.ptr = getelementptr inbounds %struct.GenericUniformHeader, ptr addrspace(2) %objectHeader, i64 0, i32 0, i32 0, i64 1
  %flags = load i32, ptr addrspace(2) %flags.ptr, align 4
  %add.ptr = getelementptr inbounds %struct.GenericUniformHeader, ptr addrspace(2) %objectHeader, i64 1
  %copyload = load <4 x float>, ptr addrspace(2) %add.ptr, align 16
  %slot = and i32 %flags, 3
  %idx = zext i32 %slot to i64
  %rect.ptr = getelementptr inbounds %struct.GenericUniformHeader, ptr addrspace(2) %objectHeader, i64 %idx
  %rect = load <4 x float>, ptr addrspace(2) %rect.ptr, align 16
  %pos = fadd fast <4 x float> %copyload, %rect
  store <4 x float> %pos, ptr addrspace(1) %out, align 16
  ret void
}

!air.kernel = !{!0}
!0 = !{ptr @k, !1, !2}
!1 = !{}
!2 = !{!3, !4}
!3 = !{i32 0, !"air.buffer", !"air.location_index", i32 0, i32 1, !"air.write", !"air.address_space", i32 1, !"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 16, !"air.arg_type_name", !"float4*", !"air.arg_name", !"out"}
!4 = !{i32 1, !"air.buffer", !"air.location_index", i32 1, i32 1, !"air.read", !"air.address_space", i32 2, !"air.struct_type_info", !5, !"air.arg_type_size", i32 16, !"air.arg_type_align_size", i32 4, !"air.arg_type_name", !"GenericUniformHeader", !"air.arg_name", !"objectHeader"}
!5 = !{i32 0, i32 16, i32 0, !"packed_uint4", !"header"}
