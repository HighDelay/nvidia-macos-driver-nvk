/* SPDX-License-Identifier: MIT */
/* Exercise the native Vulkan/VMA staging allocation used by Minecraft 26.3. */
import java.nio.ByteBuffer;
import java.nio.LongBuffer;
import java.util.ArrayList;
import java.util.HashMap;
import org.lwjgl.PointerBuffer;
import org.lwjgl.system.MemoryStack;
import org.lwjgl.system.MemoryUtil;
import org.lwjgl.util.vma.*;
import org.lwjgl.vulkan.*;
import static org.lwjgl.util.vma.Vma.*;
import static org.lwjgl.vulkan.VK10.*;
import static org.lwjgl.vulkan.VK12.*;

public final class NativeMappedMemoryProbe {
    private static final int BLOCK_BYTES = 16 << 20;
    private record Buffer(long buffer, long allocation, long address) {}
    private static final class HostAllocator implements AutoCloseable {
        private final HashMap<Long, Long> sizes = new HashMap<>();
        private long allocate(long size, long alignment) {
            // VMA requests alignment 2 for metadata; Darwin's aligned_alloc
            // requires at least pointer alignment. Minecraft supplies callbacks.
            long pointer = MemoryUtil.nmemAlignedAlloc(Math.max(alignment, 8), size);
            if (pointer != 0) sizes.put(pointer, size);
            return pointer;
        }
        private void release(long pointer) {
            if (pointer != 0) {
                sizes.remove(pointer);
                MemoryUtil.nmemAlignedFree(pointer);
            }
        }
        final VkAllocationFunction allocate = VkAllocationFunction.create(
            (user, size, alignment, scope) -> allocate(size, alignment));
        final VkReallocationFunction reallocate = VkReallocationFunction.create((user, original, size, alignment, scope) -> {
            if (size == 0) { release(original); return 0; }
            long pointer = allocate(size, alignment);
            if (pointer != 0 && original != 0) {
                MemoryUtil.memCopy(original, pointer, Math.min(size, sizes.get(original)));
                release(original);
            }
            return pointer;
        });
        final VkFreeFunction free = VkFreeFunction.create((user, pointer) -> release(pointer));
        public void close() {
            allocate.free(); reallocate.free(); free.free();
            if (!sizes.isEmpty()) throw new IllegalStateException("VMA host allocation leak");
        }
    }
    private static void check(int result, String operation) {
        if (result != VK_SUCCESS)
            throw new IllegalStateException(operation + ": VkResult=" + result);
    }
    private static Buffer allocate(long allocator, int required, int access, int usage,
                                   MemoryStack stack, VkPhysicalDeviceMemoryProperties memory) {
        VkBufferCreateInfo info = VkBufferCreateInfo.calloc(stack).sType$Default()
            .size(BLOCK_BYTES).usage(471).sharingMode(VK_SHARING_MODE_EXCLUSIVE);
        VmaAllocationCreateInfo alloc = VmaAllocationCreateInfo.calloc(stack)
            .usage(usage).requiredFlags(required).flags(access);
        LongBuffer buffer = stack.mallocLong(1);
        PointerBuffer allocation = stack.mallocPointer(1), pointer = stack.mallocPointer(1);
        VmaAllocationInfo details = VmaAllocationInfo.calloc(stack);
        check(vmaCreateBuffer(allocator, info, alloc, buffer, allocation, details), "vmaCreateBuffer");
        long address = 0;
        int type = details.memoryType();
        System.out.printf("allocation type=%d flags=0x%x heap=%d bytes=%d%n", type,
            memory.memoryTypes(type).propertyFlags(), memory.memoryTypes(type).heapIndex(), BLOCK_BYTES);
        if ((required & VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT) != 0) {
            int result = vmaMapMemory(allocator, allocation.get(0), pointer);
            if (result != VK_SUCCESS) {
                vmaDestroyBuffer(allocator, buffer.get(0), allocation.get(0));
                check(result, "vmaMapMemory");
            }
            address = pointer.get(0);
        }
        return new Buffer(buffer.get(0), allocation.get(0), address);
    }
    public static void main(String[] args) {
        int blocks = args.length == 0 ? 24 : Integer.parseInt(args[0]);
        if (blocks < 1 || blocks > 64) throw new IllegalArgumentException("block count must be 1..64");
        try (MemoryStack stack = MemoryStack.stackPush(); HostAllocator host = new HostAllocator()) {
            VkApplicationInfo app = VkApplicationInfo.calloc(stack).sType$Default()
                .pApplicationName(stack.UTF8("native mapped-memory regression")).apiVersion(VK_API_VERSION_1_2);
            PointerBuffer handle = stack.mallocPointer(1);
            check(vkCreateInstance(VkInstanceCreateInfo.calloc(stack).sType$Default().pApplicationInfo(app), null, handle), "vkCreateInstance");
            VkInstance instance = new VkInstance(handle.get(0), VkInstanceCreateInfo.calloc(stack).sType$Default().pApplicationInfo(app));
            try {
                var count = stack.mallocInt(1);
                check(vkEnumeratePhysicalDevices(instance, count, null), "vkEnumeratePhysicalDevices");
                if (count.get(0) < 1) throw new IllegalStateException("No Vulkan device");
                PointerBuffer physicals = stack.mallocPointer(count.get(0));
                check(vkEnumeratePhysicalDevices(instance, count, physicals), "vkEnumeratePhysicalDevices");
                VkPhysicalDevice physical = new VkPhysicalDevice(physicals.get(0), instance);
                VkPhysicalDeviceMemoryProperties memory = VkPhysicalDeviceMemoryProperties.calloc(stack);
                vkGetPhysicalDeviceMemoryProperties(physical, memory);
                for (int i = 0; i < memory.memoryHeapCount(); i++)
                    System.out.printf("heap %d bytes=%d flags=0x%x%n", i, memory.memoryHeaps(i).size(), memory.memoryHeaps(i).flags());
                for (int i = 0; i < memory.memoryTypeCount(); i++)
                    System.out.printf("type %d flags=0x%x heap=%d%n", i, memory.memoryTypes(i).propertyFlags(), memory.memoryTypes(i).heapIndex());
                vkGetPhysicalDeviceQueueFamilyProperties(physical, count, null);
                VkQueueFamilyProperties.Buffer families = VkQueueFamilyProperties.calloc(count.get(0), stack);
                vkGetPhysicalDeviceQueueFamilyProperties(physical, count, families);
                int family = 0;
                while (family < families.capacity() && (families.get(family).queueFlags() & VK_QUEUE_GRAPHICS_BIT) == 0) family++;
                if (family == families.capacity()) throw new IllegalStateException("No graphics queue");
                VkDeviceQueueCreateInfo.Buffer queues = VkDeviceQueueCreateInfo.calloc(1, stack);
                queues.get(0).sType$Default().queueFamilyIndex(family).pQueuePriorities(stack.floats(1));
                VkDeviceCreateInfo deviceInfo = VkDeviceCreateInfo.calloc(stack).sType$Default().pQueueCreateInfos(queues);
                check(vkCreateDevice(physical, deviceInfo, null, handle), "vkCreateDevice");
                VkDevice device = new VkDevice(handle.get(0), physical, deviceInfo);
                long allocator = 0, pool = 0, fence = 0;
                ArrayList<Buffer> buffers = new ArrayList<>();
                try {
                    VmaVulkanFunctions functions = VmaVulkanFunctions.calloc(stack).set(instance, device);
                    VmaAllocatorCreateInfo allocatorInfo = VmaAllocatorCreateInfo.calloc(stack)
                        .physicalDevice(physical).device(device).instance(instance)
                        .pAllocationCallbacks(VkAllocationCallbacks.calloc(stack)
                            .pfnAllocation(host.allocate).pfnReallocation(host.reallocate).pfnFree(host.free))
                        .vulkanApiVersion(VK_API_VERSION_1_2).pVulkanFunctions(functions);
                    check(vmaCreateAllocator(allocatorInfo, handle), "vmaCreateAllocator");
                    allocator = handle.get(0);
                    int coherent = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT | VK_MEMORY_PROPERTY_HOST_COHERENT_BIT;
                    for (int i = 0; i < blocks; i++) {
                        Buffer buffer = allocate(allocator, coherent, VMA_ALLOCATION_CREATE_HOST_ACCESS_SEQUENTIAL_WRITE_BIT,
                            VMA_MEMORY_USAGE_AUTO_PREFER_HOST, stack, memory);
                        buffers.add(buffer);
                        ByteBuffer bytes = MemoryUtil.memByteBuffer(buffer.address(), BLOCK_BYTES);
                        for (int offset = 0; offset < BLOCK_BYTES; offset += 4) bytes.putInt(offset, offset ^ i ^ 0x19670423);
                    }
                    Buffer source = buffers.get(blocks - 1);
                    Buffer gpu = allocate(allocator, VK_MEMORY_PROPERTY_DEVICE_LOCAL_BIT, 0,
                        VMA_MEMORY_USAGE_AUTO_PREFER_DEVICE, stack, memory);
                    buffers.add(gpu);
                    Buffer readback = allocate(allocator, coherent | VK_MEMORY_PROPERTY_HOST_CACHED_BIT,
                        VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT, VMA_MEMORY_USAGE_AUTO_PREFER_HOST, stack, memory);
                    buffers.add(readback);
                    LongBuffer object = stack.mallocLong(1);
                    check(vkCreateCommandPool(device, VkCommandPoolCreateInfo.calloc(stack).sType$Default().queueFamilyIndex(family), null, object), "vkCreateCommandPool");
                    pool = object.get(0);
                    check(vkAllocateCommandBuffers(device, VkCommandBufferAllocateInfo.calloc(stack).sType$Default()
                        .commandPool(pool).level(VK_COMMAND_BUFFER_LEVEL_PRIMARY).commandBufferCount(1), handle), "vkAllocateCommandBuffers");
                    VkCommandBuffer command = new VkCommandBuffer(handle.get(0), device);
                    check(vkBeginCommandBuffer(command, VkCommandBufferBeginInfo.calloc(stack).sType$Default()), "vkBeginCommandBuffer");
                    VkBufferCopy.Buffer region = VkBufferCopy.calloc(1, stack).size(BLOCK_BYTES);
                    vkCmdCopyBuffer(command, source.buffer(), gpu.buffer(), region);
                    VkMemoryBarrier.Buffer barrier = VkMemoryBarrier.calloc(1, stack).sType$Default()
                        .srcAccessMask(VK_ACCESS_TRANSFER_WRITE_BIT).dstAccessMask(VK_ACCESS_TRANSFER_READ_BIT);
                    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_TRANSFER_BIT, 0, barrier, null, null);
                    vkCmdCopyBuffer(command, gpu.buffer(), readback.buffer(), region);
                    barrier.get(0).dstAccessMask(VK_ACCESS_HOST_READ_BIT);
                    vkCmdPipelineBarrier(command, VK_PIPELINE_STAGE_TRANSFER_BIT, VK_PIPELINE_STAGE_HOST_BIT, 0, barrier, null, null);
                    check(vkEndCommandBuffer(command), "vkEndCommandBuffer");
                    check(vkCreateFence(device, VkFenceCreateInfo.calloc(stack).sType$Default(), null, object), "vkCreateFence");
                    fence = object.get(0);
                    vkGetDeviceQueue(device, family, 0, handle);
                    VkQueue queue = new VkQueue(handle.get(0), device);
                    VkSubmitInfo.Buffer submit = VkSubmitInfo.calloc(1, stack).sType$Default().pCommandBuffers(stack.pointers(command));
                    check(vkQueueSubmit(queue, submit, fence), "vkQueueSubmit");
                    check(vkWaitForFences(device, fence, true, 10_000_000_000L), "vkWaitForFences");
                    ByteBuffer data = MemoryUtil.memByteBuffer(readback.address(), BLOCK_BYTES);
                    for (int offset = 0; offset < BLOCK_BYTES; offset += 4)
                        if (data.getInt(offset) != (offset ^ (blocks - 1) ^ 0x19670423))
                            throw new IllegalStateException("GPU readback mismatch at " + offset);
                    System.out.printf("PASS: %d MiB persistently mapped; device-local GPU copy and full readback match%n", blocks * 16);
                } finally {
                    vkDeviceWaitIdle(device);
                    if (fence != 0) vkDestroyFence(device, fence, null);
                    if (pool != 0) vkDestroyCommandPool(device, pool, null);
                    for (Buffer buffer : buffers) {
                        if (buffer.address() != 0) vmaUnmapMemory(allocator, buffer.allocation());
                        vmaDestroyBuffer(allocator, buffer.buffer(), buffer.allocation());
                    }
                    if (allocator != 0) vmaDestroyAllocator(allocator);
                    vkDestroyDevice(device, null);
                }
            } finally { vkDestroyInstance(instance, null); }
        }
    }
}
