#pragma once
#include <array>
#include <condition_variable>
#include <exception>
#include <functional>
#include <mutex>
#include <thread>
#include <utility>

namespace nvkbridge {
// Capacity includes the running job. No submission or registry lock may be
// held while posting, draining or stopping. The owner joins before releasing
// the device; the worker never owns its owner or joins itself.
class PresentWorker {
public:
   using Task = std::function<void()>;
   using Failure = std::function<void(std::exception_ptr)>;
   static constexpr unsigned capacity = 2;
   explicit PresentWorker(Failure failure):failure_(std::move(failure)),thread_([this]{run();}) {}
   PresentWorker(const PresentWorker &) = delete;
   PresentWorker &operator=(const PresentWorker &) = delete;
   ~PresentWorker() { stop(); }
   bool post(Task task) {
      std::unique_lock<std::mutex> lock(mutex_);
      changed_.wait(lock,[&]{return stopping_ || outstanding_<capacity;});
      if(stopping_)return false;
      tasks_[(head_+queued_)%capacity]=std::move(task);
      ++queued_;++outstanding_;changed_.notify_all();return true;
   }
   void drain() {
      std::unique_lock<std::mutex> lock(mutex_);
      changed_.wait(lock,[&]{return !outstanding_;});
   }
   void stop() {
      { std::lock_guard<std::mutex> lock(mutex_);stopping_=true;changed_.notify_all(); }
      if(thread_.joinable())thread_.join();
   }
private:
   void run() {
      for(;;) {
         Task task;
         { std::unique_lock<std::mutex> lock(mutex_);
           changed_.wait(lock,[&]{return queued_ || stopping_;});
           if(!queued_)return;
           task=std::move(tasks_[head_]);head_=(head_+1)%capacity;--queued_;
         }
         try { task(); } catch(...) { failure_(std::current_exception()); }
         // Release job ownership before notifying idle/teardown callers.
         task={};
         { std::lock_guard<std::mutex> lock(mutex_);--outstanding_;changed_.notify_all(); }
      }
   }
   Failure failure_;
   std::mutex mutex_;
   std::condition_variable changed_;
   std::array<Task,capacity> tasks_;
   unsigned head_{},queued_{},outstanding_{};
   bool stopping_{};
   std::thread thread_;
};
}
